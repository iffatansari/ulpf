import asyncio
import json
import os
import uuid
from typing import Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from opensearchpy import OpenSearch

from schema.raw_event import RawEventEnvelope
from schema.normalized_event import NormalizedEvent
from schema.dlq_record import DLQRecord

from parsers.cef_parser import parse_cef_log
from parsers.json_parser import parse_json_log
from parsers.syslog_parser import parse_syslog
from parsers.drain_fallback import PARSER_ID as DRAIN3_PARSER_ID, parse_drain
from parsers import custom_chain

from dlq.reprocess import (
    HEADER_REPROCESS_ID,
    HEADER_REPROCESS_OF,
    apply_attempt_outcome,
)


REDPANDA_BROKER = os.getenv("REDPANDA_BROKER", "redpanda:29092")

RAW_TOPIC = os.getenv("RAW_TOPIC", "logs.raw")
NORMALIZED_TOPIC = os.getenv("NORMALIZED_TOPIC", "logs.normalized")
DLQ_TOPIC = os.getenv("DLQ_TOPIC", "logs.dlq")

OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://opensearch:9200")

# Format hints that have a parser of their own. Anything else -- including
# "unknown", "auto", "raw", "text", "leef", "" and None -- gets the full
# probe plus the Drain3 fallback tier, so a hint nobody recognises still has
# a real chance of being normalized instead of dead-ending in the DLQ.
_HINTS_WITH_DEDICATED_PARSER = frozenset({"cef", "json", "syslog"})

# One table rather than three near-identical branches. The declared parser is
# tried first, and only if it declines does the Drain3 tier get a turn -- a
# hint is a preference, not a commitment that ends in the DLQ.
_DEDICATED_PARSERS = {
    "cef": ("cef-parser-v1", parse_cef_log),
    "json": ("json-parser-v1", parse_json_log),
    "syslog": ("syslog-parser-v1", parse_syslog),
}

# The order an unhinted line is probed in, before the Drain3 fallback.
_PROBE_CHAIN = tuple(_DEDICATED_PARSERS.values())


SILVER_INDEX = os.getenv("SILVER_INDEX", "ulpf-silver")
DLQ_INDEX = os.getenv("DLQ_INDEX", "ulpf-dlq")
BRONZE_INDEX = os.getenv("BRONZE_INDEX", "ulpf-bronze")

GROUP_ID = "ulpf-orchestrator-group"

BRONZE_MAPPING = {
    "mappings": {
        "properties": {
            "event_id": {"type": "keyword"},
            "ingested_at": {"type": "date"},
            "source_id": {"type": "keyword"},
            "source_type": {"type": "keyword"},
            "transport": {"type": "keyword"},
            "format_hint": {"type": "keyword"},
            "raw_payload": {
                "type": "text",
                "fields": {
                    "keyword": {
                        "type": "keyword",
                        "ignore_above": 32766,
                    }
                },
            },
            "bronze_uri": {"type": "keyword"},
            "collector_id": {"type": "keyword"},
            "envelope_schema_version": {"type": "keyword"},
            "upload_id": {"type": "keyword"},
        }
    }
}


# Silver/DLQ stay "dynamic" for everything except the sort fields.
# Queries order Silver events by `time` and DLQ records by
# `first_seen_at`; an empty index has no mapping for those, so
# OpenSearch rejects a sort on them with a 400. Declaring the date
# types up-front (before any document exists) fixes the fresh-start
# crash while leaving the rest of the document dynamically mapped.
#
# `extensions.original_json` is a lossless passthrough of the source
# document, so the same key legitimately arrives as a number in one
# event and a string in the next. Dynamic mapping resolves a field's
# type from the FIRST document it sees and then rejects every later
# document that disagrees -- which used to fail the whole Silver
# write and lose the event. Forcing that subtree to `keyword` makes
# both shapes indexable; the promoted fields above it stay typed.
BLOB_AS_KEYWORD = {
    "path_match": "extensions.original_json.*",
    "mapping": {"type": "keyword", "ignore_above": 2048},
}

SILVER_MAPPING = {
    "mappings": {
        "dynamic_templates": [
            {"original_json_as_keyword": BLOB_AS_KEYWORD},
        ],
        "properties": {
            "time": {"type": "date"},
        },
    }
}

DLQ_MAPPING = {
    "mappings": {
        "properties": {
            "first_seen_at": {"type": "date"},
            "last_attempt_at": {"type": "date"},
        }
    }
}


def ensure_index(es: OpenSearch, index: str, body: dict):
    """
    Safely create an OpenSearch index if it does not exist.
    Does not touch an existing index.
    """
    if es.indices.exists(index=index):
        return

    es.indices.create(index=index, body=body)
    print(f"Created OpenSearch index {index}", flush=True)


def extract_upload_id(headers) -> Optional[str]:
    """
    Read the optional upload_id Kafka header without requiring it.

    The File Collector attaches this header so a raw event can be
    traced back to the upload job that produced it. Other collectors
    (UDP/HTTP) do not send it and must keep working unchanged.
    """
    for key, value in (headers or []):
        if key == "upload_id" and value is not None:
            if isinstance(value, bytes):
                return value.decode("utf-8")
            return str(value)
    return None


def extract_replay_headers(headers) -> tuple[Optional[str], Optional[str]]:
    """
    Read the Module 3 replay headers set by the reprocess service.

    Returns (reprocess_id, dlq_id). Both are None for ordinary ingested
    traffic, which is what keeps this a transparent change: without these
    headers the pipeline behaves exactly as it did in Module 2.

    The headers are what let a replay update the EXISTING DLQ record
    instead of appending a new one. Without them the failure path cannot
    tell a first-time failure (mint a new record) from a replay that failed
    again (update the record it came from), and the DLQ would fill up with
    duplicates of the same event.
    """
    reprocess_id = None
    dlq_id = None
    for key, value in (headers or []):
        if value is None:
            continue
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if key == HEADER_REPROCESS_ID:
            reprocess_id = str(value)
        elif key == HEADER_REPROCESS_OF:
            dlq_id = str(value)
    return reprocess_id, dlq_id


def try_parser(
    parser,
    parser_id: str,
    raw_event: RawEventEnvelope,
):
    """
    Try one parser against the raw event.

    Returns:
        (NormalizedEvent or None, parser_id)
    """

    try:
        parsed = parser(
            raw_event.raw_payload,
            raw_event.event_id,
            raw_event.source_id,
        )

        if parsed is None:
            return None, parser_id

        normalized = NormalizedEvent.model_validate(
            parsed.model_dump()
        )

        return normalized, parser_id

    except Exception as exc:
        print(
            f"Parser {parser_id} failed for "
            f"{raw_event.event_id}: {exc}",
            flush=True,
        )

        return None, parser_id


def _try_custom_parsers(
    raw_event: RawEventEnvelope,
    parsers_attempted: list[str],
    opensearch: Optional[OpenSearch] = None,
) -> Optional[NormalizedEvent]:
    """
    Give registered custom parsers their turn, before the Drain3 tier.

    A custom parser is registered for a vendor format the built-ins do not
    know, so it runs only once they have all declined -- and it runs before
    Drain3, because Drain3 would otherwise claim the same line and label it by
    template, which is indistinguishable, to the operator who just registered a
    parser, from the parser not working at all.

    A registry that cannot be read is not an error here: the built-in chain has
    already had its turn, and Drain3 still follows.
    """

    try:
        parsers = custom_chain.custom_parsers(opensearch)
    except Exception as exc:
        print(f"Custom parser tier unavailable: {exc}", flush=True)
        return None

    for parser_id, parser in parsers:

        normalized, attempted_parser_id = try_parser(
            parser,
            parser_id,
            raw_event,
        )

        parsers_attempted.append(attempted_parser_id)

        if normalized is not None:

            print(
                f"Recovered via custom parser {attempted_parser_id} "
                f"for event {raw_event.event_id}",
                flush=True,
            )

            return normalized

    return None


def _try_drain_fallback(
    raw_event: RawEventEnvelope,
    parsers_attempted: list[str],
) -> Optional[NormalizedEvent]:
    """
    Give the Drain3 tier its turn and report whether it answered.

    Split out because every failure path needs it: the fallback is what stands
    between a rejected line and the DLQ.
    """
    normalized, attempted_parser_id = try_parser(
        parse_drain,
        "drain3-fallback-v1",
        raw_event,
    )

    parsers_attempted.append(attempted_parser_id)

    if normalized is not None:
        print(
            f"Recovered via {attempted_parser_id} for event "
            f"{raw_event.event_id}",
            flush=True,
        )

    return normalized


def normalize_raw_event(
    raw_event: RawEventEnvelope,
    opensearch: Optional[OpenSearch] = None,
) -> tuple[Optional[NormalizedEvent], list[str]]:
    """
    Decide which parser(s) should be tried.

    A format hint is a PREFERENCE, not a commitment. When the hinted parser
    parses the line, it wins and the fallback is never touched. When it
    declines, the remaining tiers still get a turn before anything is
    rejected, because a hint that no longer matches the traffic (a source
    reconfigured, a vendor changing its format, a truncated write) used to
    dead-end straight into the DLQ even though the fallback existed precisely
    to catch "no dedicated parser matched".

    That distinction matters: every event minted as a DLQ record is work an
    operator has to triage and replay, so the chain should only reject once
    every tier -- including the custom parsers an operator registered for this
    very traffic -- has had its turn.

    Known format:
        Use the corresponding parser, then the custom tier, then Drain3.

    Unknown format:
        Probe all known parsers until one successfully
        produces a valid NormalizedEvent, then the custom
        tier, then the Drain3 fallback tier.

    `opensearch` is threaded in by the consumer loop so the custom tier can
    read the registry over the connection the process already holds. It is
    optional because the other caller, the API's DLQ dry run, has no client to
    offer and lets the tier open its own.
    """

    parsers_attempted = []

    # ---------------------------------------------------------
    # 1-3. A hint that has a parser of its own
    #
    # One table instead of three near-identical branches: the behaviour
    # has to be identical for cef/json/syslog, and duplicating it three
    # times is how only some of them got the fall-through.
    # ---------------------------------------------------------
    dedicated = _DEDICATED_PARSERS.get(raw_event.format_hint)

    if dedicated is not None:
        parser_id, parser = dedicated

        normalized, attempted_parser_id = try_parser(
            parser,
            parser_id,
            raw_event,
        )

        parsers_attempted.append(attempted_parser_id)

        if normalized is not None:
            return normalized, parsers_attempted

        # Declined. Fall through rather than reject.
        recovered = _try_custom_parsers(raw_event, parsers_attempted, opensearch)
        if recovered is not None:
            return recovered, parsers_attempted

        return _try_drain_fallback(raw_event, parsers_attempted), parsers_attempted

    # ---------------------------------------------------------
    # 4. NO DEDICATED PARSER FOR THIS HINT
    #
    # Do NOT use transport to decide the parser.
    #
    # Probe all known parsers, then fall through to the custom
    # tier and the Drain3 fallback tier.
    #
    # This deliberately covers every hint that is not cef/json/
    # syslog -- "unknown", but also "auto", "raw", "text", "leef",
    # "" and None. Those hints have no parser of their own, and
    # previously fell straight through to the DLQ without ever
    # giving Drain3 a chance. A mislabelled or vendor-specific hint
    # should still get the full probe rather than being dead-ended.
    # ---------------------------------------------------------

    for parser_id, parser in _PROBE_CHAIN:

        normalized, attempted_parser_id = try_parser(
            parser,
            parser_id,
            raw_event,
        )

        parsers_attempted.append(attempted_parser_id)

        if normalized is not None:

            print(
                f"Unknown format identified as "
                f"{parser_id} for event "
                f"{raw_event.event_id}",
                flush=True,
            )

            return normalized, parsers_attempted

    recovered = _try_custom_parsers(raw_event, parsers_attempted, opensearch)
    if recovered is not None:
        return recovered, parsers_attempted

    return _try_drain_fallback(raw_event, parsers_attempted), parsers_attempted


def create_dlq_record(
    raw_event: RawEventEnvelope,
    parsers_attempted: list[str],
    classification: Optional[str] = None,
    status: Optional[str] = None,
) -> DLQRecord:

    reject_reason: Optional[str] = None

    # A record that normalized but could not be indexed.
    if classification is not None:
        pass

    # The whole chain ran, including the fallback tier, and the fallback still
    # declined. That is not "no parser matched" and not "unknown format" --
    # every parser had its turn. The cause is that Drain3 found no field it
    # could label as an identity, so there is nothing to stand an event on.
    # Labelling it distinctly is the difference between an operator reading
    # "the format is unknown, let me add a parser" and "this line is
    # contentless, and no parser would have saved it".
    elif DRAIN3_PARSER_ID in parsers_attempted:

        classification = "no_recoverable_identity"
        status = "parse_failure"
        reject_reason = (
            "every parser was tried including the drain3 fallback, which "
            "declined: no src/dst/user/mac field in the line could be labeled "
            "as an identity to stand an event on"
        )

    # Unknown format where every known parser failed.
    elif raw_event.format_hint == "unknown":

        classification = "format_unidentified"
        status = "unknown"

    # Known format but its parser could not parse it.
    elif parsers_attempted:

        classification = "no_parser_match"
        status = "parse_failure"

    # Nothing was available to identify/parse it.
    else:

        classification = "unsupported_format"
        status = "unknown"

    return DLQRecord(
        dlq_id=str(uuid.uuid4()),
        raw_event_id=raw_event.event_id,
        raw_payload=raw_event.raw_payload,
        parsers_attempted=parsers_attempted,
        status=status,
        classification=classification,
        metadata={
            "source_id": raw_event.source_id,
            "source_type": raw_event.source_type,
            "transport": raw_event.transport,
            "format_hint": raw_event.format_hint,
            **({"reject_reason": reject_reason} if reject_reason else {}),
        },
    )


async def route_index_rejection(
    producer: AIOKafkaProducer,
    opensearch: OpenSearch,
    raw_event: RawEventEnvelope,
    normalized: Optional[NormalizedEvent],
    stage: str,
    error: Exception,
) -> None:
    """
    A record that parsed cleanly but could not be indexed still has to be
    visible. Persist it in the DLQ with `index_rejected` so the loss shows up
    in the UI with the reason attached, instead of vanishing in a log line.
    """
    record = create_dlq_record(
        raw_event,
        [normalized.parser_id] if normalized is not None else [],
        classification="index_rejected",
        status="index_failure",
    )
    document = record.model_dump(mode="json")
    document["metadata"] = {
        **(document.get("metadata") or {}),
        "rejected_stage": stage,
        "rejected_parser": normalized.parser_id if normalized else None,
        "index_error": f"{type(error).__name__}: {error}"[:2000],
    }
    try:
        opensearch.index(
            index=DLQ_INDEX,
            id=record.dlq_id,
            body=document,
        )
    except Exception as dlq_exc:
        print(
            f"DLQ write also failed for {raw_event.event_id}: {dlq_exc}",
            flush=True,
        )
    try:
        await producer.send_and_wait(
            DLQ_TOPIC,
            json.dumps(document).encode("utf-8"),
        )
    except Exception as kafka_exc:
        print(
            f"DLQ publish failed for {raw_event.event_id}: {kafka_exc}",
            flush=True,
        )


async def main():

    consumer = AIOKafkaConsumer(
        RAW_TOPIC,
        bootstrap_servers=REDPANDA_BROKER,
        group_id=GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda v: v.decode("utf-8"),
    )

    producer = AIOKafkaProducer(
        bootstrap_servers=REDPANDA_BROKER
    )

    opensearch = OpenSearch(
        hosts=[OPENSEARCH_URL],
        timeout=10,
    )

    ensure_index(opensearch, BRONZE_INDEX, BRONZE_MAPPING)
    ensure_index(opensearch, SILVER_INDEX, SILVER_MAPPING)
    ensure_index(opensearch, DLQ_INDEX, DLQ_MAPPING)

    await consumer.start()
    await producer.start()

    print(
        f"Orchestrator started. Reading {RAW_TOPIC}",
        flush=True,
    )

    try:

        async for msg in consumer:

            try:
                raw_json = json.loads(msg.value)

                raw_event = RawEventEnvelope(
                    **raw_json
                )

                # -------------------------------------------------
                # BRONZE: persist the original raw event first.
                #
                # Every raw event that enters the pipeline is
                # recorded in ulpf-bronze BEFORE any parsing is
                # attempted. Document _id = raw_event.event_id so
                # the normalized/DLQ raw_event_id always points
                # back to the lossless original payload.
                #
                # If Bronze persistence fails, the event is not
                # silently forwarded; the error surfaces below.
                # -------------------------------------------------

                raw_document = raw_event.model_dump(
                    mode="json"
                )

                # File Collector attaches an upload_id Kafka header.
                # It is stored as additive lineage metadata on the
                # Bronze document (the envelope schema is unchanged).
                upload_id = extract_upload_id(msg.headers)
                if upload_id:
                    raw_document["upload_id"] = upload_id

                # Module 3: a replayed event carries the reprocess headers.
                # Absent for normal traffic.
                reprocess_id, replay_dlq_id = extract_replay_headers(
                    msg.headers
                )
                is_replay = reprocess_id is not None and replay_dlq_id is not None

                opensearch.index(
                    index=BRONZE_INDEX,
                    id=raw_event.event_id,
                    body=raw_document,
                )

                normalized, parsers_attempted = normalize_raw_event(
                    raw_event,
                    opensearch,
                )

                # -------------------------------------------------
                # SUCCESS → Normalized pipeline
                # -------------------------------------------------

                if normalized is not None:

                    normalized_document = normalized.model_dump(
                        mode="json"
                    )

                    try:
                        opensearch.index(
                            index=SILVER_INDEX,
                            id=normalized.event_id,
                            body=normalized_document,
                        )
                    except Exception as index_exc:
                        # The parser succeeded but OpenSearch refused the
                        # document (mapping conflict, strict mapping, ...).
                        # Losing it here would hide it from every count, so
                        # it goes to the DLQ with its own classification.
                        print(
                            f"Silver write rejected for "
                            f"{raw_event.event_id}: {index_exc}",
                            flush=True,
                        )
                        await route_index_rejection(
                            producer=producer,
                            opensearch=opensearch,
                            raw_event=raw_event,
                            normalized=normalized,
                            stage="silver",
                            error=index_exc,
                        )
                        continue

                    await producer.send_and_wait(
                        NORMALIZED_TOPIC,
                        json.dumps(
                            normalized_document
                        ).encode("utf-8"),
                    )

                    print(
                        f"Normalized event "
                        f"{raw_event.event_id} "
                        f"using {normalized.parser_id}",
                        flush=True,
                    )

                    # Module 3: a replay that reaches Silver RECOVERS the
                    # original DLQ record. The record is kept, not deleted,
                    # so "was this originally a failure?" stays answerable.
                    if is_replay:
                        apply_attempt_outcome(
                            es=opensearch,
                            dlq_id=replay_dlq_id,
                            reprocess_id=reprocess_id,
                            result="recovered",
                            reason=f"replayed via {normalized.parser_id}",
                        )
                        print(
                            f"Reprocess {reprocess_id}: DLQ "
                            f"{replay_dlq_id} -> recovered",
                            flush=True,
                        )

                    continue

                # -------------------------------------------------
                # FAILURE → DLQ
                # -------------------------------------------------

                # Module 3: a replay that fails AGAIN must update the
                # record it came from, never mint a second one. Creating a
                # fresh DLQRecord here would give one unparseable event N
                # documents after N replays, inflating the DLQ count and
                # destroying the audit trail.
                if is_replay:
                    apply_attempt_outcome(
                        es=opensearch,
                        dlq_id=replay_dlq_id,
                        reprocess_id=reprocess_id,
                        result="failed",
                        reason="no parser produced a valid normalized event",
                    )
                    await producer.send_and_wait(
                        DLQ_TOPIC,
                        json.dumps(
                            {
                                "dlq_id": replay_dlq_id,
                                "raw_event_id": raw_event.event_id,
                                "reprocess_id": reprocess_id,
                                "reprocessed": True,
                                "resolution_status": "unresolved",
                                "parsers_attempted": parsers_attempted,
                            }
                        ).encode("utf-8"),
                    )
                    print(
                        f"Reprocess {reprocess_id}: DLQ "
                        f"{replay_dlq_id} still unresolved | "
                        f"parsers_attempted={parsers_attempted}",
                        flush=True,
                    )
                    continue

                dlq_record = create_dlq_record(
                    raw_event,
                    parsers_attempted,
                )

                dlq_document = dlq_record.model_dump(
                    mode="json"
                )

                await producer.send_and_wait(
                    DLQ_TOPIC,
                    json.dumps(
                        dlq_document
                    ).encode("utf-8"),
                )

                opensearch.index(
                    index=DLQ_INDEX,
                    id=dlq_record.dlq_id,
                    body=dlq_document,
                )

                print(
                    f"Event {raw_event.event_id} "
                    f"sent to DLQ | "
                    f"classification="
                    f"{dlq_record.classification} | "
                    f"parsers_attempted="
                    f"{parsers_attempted}",
                    flush=True,
                )

            except Exception as exc:

                print(
                    f"Error processing Kafka message: {exc}",
                    flush=True,
                )

    finally:

        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())