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
from parsers.drain_fallback import parse_drain


REDPANDA_BROKER = os.getenv("REDPANDA_BROKER", "redpanda:29092")

RAW_TOPIC = os.getenv("RAW_TOPIC", "logs.raw")
NORMALIZED_TOPIC = os.getenv("NORMALIZED_TOPIC", "logs.normalized")
DLQ_TOPIC = os.getenv("DLQ_TOPIC", "logs.dlq")

OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://opensearch:9200")

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
SILVER_MAPPING = {
    "mappings": {
        "properties": {
            "time": {"type": "date"},
        }
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


def normalize_raw_event(
    raw_event: RawEventEnvelope,
) -> tuple[Optional[NormalizedEvent], list[str]]:
    """
    Decide which parser(s) should be tried.

    Known format:
        Use the corresponding parser.

    Unknown format:
        Probe all known parsers until one successfully
        produces a valid NormalizedEvent.
    """

    parsers_attempted = []

    # ---------------------------------------------------------
    # 1. CEF explicitly identified
    # ---------------------------------------------------------
    if raw_event.format_hint == "cef":

        normalized, parser_id = try_parser(
            parse_cef_log,
            "cef-parser-v1",
            raw_event,
        )

        parsers_attempted.append(parser_id)

        if normalized is not None:
            return normalized, parsers_attempted

        return None, parsers_attempted

    # ---------------------------------------------------------
    # 2. JSON explicitly identified
    # ---------------------------------------------------------
    if raw_event.format_hint == "json":

        normalized, parser_id = try_parser(
            parse_json_log,
            "json-parser-v1",
            raw_event,
        )

        parsers_attempted.append(parser_id)

        if normalized is not None:
            return normalized, parsers_attempted

        return None, parsers_attempted

    # ---------------------------------------------------------
    # 3. Syslog explicitly identified
    # ---------------------------------------------------------
    if raw_event.format_hint == "syslog":

        normalized, parser_id = try_parser(
            parse_syslog,
            "syslog-parser-v1",
            raw_event,
        )

        parsers_attempted.append(parser_id)

        if normalized is not None:
            return normalized, parsers_attempted

        return None, parsers_attempted

    # ---------------------------------------------------------
    # 4. UNKNOWN FORMAT
    #
    # Do NOT use transport to decide the parser.
    #
    # Probe all known parsers.
    # ---------------------------------------------------------
    if raw_event.format_hint == "unknown":

        candidate_parsers = [
            ("cef-parser-v1", parse_cef_log),
            ("json-parser-v1", parse_json_log),
            ("syslog-parser-v1", parse_syslog),
        ]

        for parser_id, parser in candidate_parsers:

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

        normalized, attempted_parser_id = try_parser(
            parse_drain,
            "drain3-fallback-v1",
            raw_event,
        )

        parsers_attempted.append(attempted_parser_id)

        if normalized is not None:

            print(
                f"Unknown format identified as "
                f"{attempted_parser_id} for event "
                f"{raw_event.event_id}",
                flush=True,
            )

            return normalized, parsers_attempted

        return None, parsers_attempted

    # ---------------------------------------------------------
    # 5. No usable format hint
    # ---------------------------------------------------------

    return None, parsers_attempted


def create_dlq_record(
    raw_event: RawEventEnvelope,
    parsers_attempted: list[str],
) -> DLQRecord:

    # Unknown format where every known parser failed.
    if raw_event.format_hint == "unknown":

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
        },
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

                opensearch.index(
                    index=BRONZE_INDEX,
                    id=raw_event.event_id,
                    body=raw_document,
                )

                normalized, parsers_attempted = normalize_raw_event(
                    raw_event
                )

                # -------------------------------------------------
                # SUCCESS → Normalized pipeline
                # -------------------------------------------------

                if normalized is not None:

                    normalized_document = normalized.model_dump(
                        mode="json"
                    )

                    await producer.send_and_wait(
                        NORMALIZED_TOPIC,
                        json.dumps(
                            normalized_document
                        ).encode("utf-8"),
                    )

                    opensearch.index(
                        index=SILVER_INDEX,
                        id=normalized.event_id,
                        body=normalized_document,
                    )

                    print(
                        f"Normalized event "
                        f"{raw_event.event_id} "
                        f"using {normalized.parser_id}",
                        flush=True,
                    )

                    continue

                # -------------------------------------------------
                # FAILURE → DLQ
                # -------------------------------------------------

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