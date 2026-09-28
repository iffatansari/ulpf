"""
Module 3 -- DLQ reprocessing service.

The single rule this module exists to enforce: a replay is NOT a second
parsing implementation. It re-publishes the ORIGINAL Bronze event back onto
`logs.raw` and lets the existing orchestrator run the identical parser
chain, Drain3 fallback, normalization and validation. If replay used its own
parsing path it would eventually disagree with the live pipeline, and the
replay result would stop meaning anything.

    DLQ record
        -> raw_event_id
        -> Bronze document (authoritative, lossless)
        -> republish to logs.raw with replay headers
        -> orchestrator consumes
        -> Silver, or the SAME DLQ record again

Bronze is the source of truth, not the DLQ's copied `raw_payload` (§6). The
copy exists for display; replay always goes back to Bronze so the lineage
Bronze event_id -> raw_event_id -> DLQ stays intact.

Replay metadata travels as Kafka headers rather than in the payload, for the
same reason the File Collector uses a header for upload_id: the envelope
schema is `extra="forbid"`, so fields cannot be smuggled into the body
without breaking every downstream reader.
"""

import importlib.util
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from opensearchpy import OpenSearch
from schema.raw_event import RawEventEnvelope
from schema.reprocess_run import ReprocessRun

import json


# Kafka headers the orchestrator reads to recognise a replay.
HEADER_REPROCESS_ID = "reprocess_id"
HEADER_REPROCESS_OF = "reprocess_of"

RAW_TOPIC = os.getenv("RAW_TOPIC", "logs.raw")
DLQ_INDEX = os.getenv("DLQ_INDEX", "ulpf-dlq")
BRONZE_INDEX = os.getenv("BRONZE_INDEX", "ulpf-bronze")
REPROCESS_RUNS_INDEX = os.getenv("REPROCESS_RUNS_INDEX", "ulpf-reprocess-runs")

# Last parser in the chain. Its verdict depends on miner state, which is why a
# dry run has to caveat it (see `dry_run_record`).
DRAIN3_PARSER_ID = "drain3-fallback-v1"


# The orchestrator lives next to this package: /app/orchestrator in the
# container layout, <repo-root>/orchestrator in a local checkout.
# api/routes/drain.py resolves it the same way so the API and the
# orchestrator always share exactly one parser implementation.
_ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

_pipeline = None


def _load_pipeline():
    """
    Load the orchestrator's `normalize_raw_event` for dry runs.

    Loaded by file path under a unique module name: the orchestrator module
    is called `main`, and the API's own entrypoint is also `main` (uvicorn
    runs `main:app` from /app/api), so a plain `import main` here would
    import the API and silently give the dry run the wrong function.

    Executing the module is side-effect free -- it only reads env vars and
    defines functions, because `asyncio.run(main())` is guarded by
    `if __name__ == "__main__"`, which is false under a synthetic name.
    """
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    spec = importlib.util.spec_from_file_location(
        "ulpf_orchestrator_pipeline", _ORCHESTRATOR_DIR / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _pipeline = module
    return module


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def envelope_from_bronze(bronze: dict) -> RawEventEnvelope:
    """
    Rebuild the original ingestion envelope from a Bronze document.

    Bronze is written from the envelope plus optional additive lineage
    (e.g. `upload_id`), and RawEventEnvelope forbids extra fields, so
    non-envelope keys are dropped before validation. `event_id` and
    `ingested_at` are preserved deliberately: the replayed event must keep
    its original identity, because Bronze and Silver both key on it. That is
    what makes a replay idempotent instead of duplicating rows.
    """
    allowed = set(RawEventEnvelope.model_fields)
    payload = {k: v for k, v in bronze.items() if k in allowed}
    return RawEventEnvelope.model_validate(payload)


def _fetch(es: OpenSearch, index: str, doc_id: str) -> Optional[dict]:
    if not es.exists(index=index, id=doc_id):
        return None
    return es.get(index=index, id=doc_id)["_source"]


def resolve_replay_targets(
    es: OpenSearch, dlq_ids: Iterable[str]
) -> tuple[list[tuple[str, dict, RawEventEnvelope]], list[dict]]:
    """
    Split the requested DLQ ids into replayable targets and errors.

    Resolving every target BEFORE publishing anything is deliberate: the run
    document needs a truthful `published_count` up front. If the count were
    written after the first publish, the orchestrator could report a
    recovery while `published_count` was still 0 and the run would be marked
    complete on its first result.
    """
    targets: list[tuple[str, dict, RawEventEnvelope]] = []
    errors: list[dict] = []

    for dlq_id in dlq_ids:
        record = _fetch(es, DLQ_INDEX, dlq_id)
        if record is None:
            errors.append({"dlq_id": dlq_id, "detail": "dlq record not found"})
            continue

        raw_event_id = record.get("raw_event_id")
        if not raw_event_id:
            errors.append({"dlq_id": dlq_id, "detail": "dlq record has no raw_event_id"})
            continue

        bronze = _fetch(es, BRONZE_INDEX, raw_event_id)
        if bronze is None:
            # Bronze is the replay source of truth, so a missing Bronze
            # document is unrecoverable here -- report it rather than
            # falling back to the DLQ's payload copy, which would break the
            # lineage guarantee in §6.
            errors.append(
                {"dlq_id": dlq_id, "detail": f"bronze event not found: {raw_event_id}"}
            )
            continue

        try:
            targets.append((dlq_id, record, envelope_from_bronze(bronze)))
        except Exception as exc:
            errors.append(
                {
                    "dlq_id": dlq_id,
                    "detail": f"bronze envelope invalid: {type(exc).__name__}: {exc}",
                }
            )

    return targets, errors


async def replay_records(
    producer,
    es: OpenSearch,
    dlq_ids: list[str],
    reason: Optional[str] = None,
) -> ReprocessRun:
    """
    Republish DLQ events onto the live pipeline and return the run to poll.

    Never blocks on parsing: the caller gets a run id immediately and the
    orchestrator advances the outcome counters as events resolve (§9).
    """
    # Collapse duplicate ids before anything else. Replaying the same record
    # twice in one run would double-publish it, double-count the run against
    # `published_count` and inflate the DLQ record's reprocess_count, all
    # while the operator only ever asked for it once.
    unique_ids: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for dlq_id in dlq_ids:
        if dlq_id in seen:
            duplicates.append(dlq_id)
            continue
        seen.add(dlq_id)
        unique_ids.append(dlq_id)

    targets, errors = resolve_replay_targets(es, unique_ids)
    for dlq_id in duplicates:
        errors.append({"dlq_id": dlq_id, "detail": "duplicate id in request, ignored"})

    now = _now()

    run = ReprocessRun(
        reprocess_id=str(uuid.uuid4()),
        created_at=now,
        started_at=now if targets else None,
        status="running" if targets else "failed",
        requested_count=len(unique_ids),
        published_count=len(targets),
        dlq_ids=[dlq_id for dlq_id, _, _ in targets],
        event_ids=[env.event_id for _, _, env in targets],
        reason=reason,
        errors=errors,
    )
    # Written before the first publish so `published_count` is already
    # correct when the orchestrator reports its first outcome.
    es.index(
        index=REPROCESS_RUNS_INDEX,
        id=run.reprocess_id,
        body=run.model_dump(mode="json"),
    )

    # Only records that actually made it onto the topic are stamped with the
    # replay intent and counted as published. A publish that never happened
    # must not be counted: the run would otherwise wait forever for an
    # outcome that cannot arrive, and the DLQ record would falsely claim an
    # attempt that was never made.
    published: list[tuple[str, dict, RawEventEnvelope]] = []

    for dlq_id, record, envelope in targets:
        try:
            await producer.send_and_wait(
                RAW_TOPIC,
                json.dumps(envelope.model_dump(mode="json")).encode("utf-8"),
                headers=[
                    # aiokafka requires header values to be bytes. Passing str
                    # raises "TypeError: Expected bytes, got str" at send time.
                    (HEADER_REPROCESS_ID, run.reprocess_id.encode("utf-8")),
                    (HEADER_REPROCESS_OF, dlq_id.encode("utf-8")),
                ],
            )
        except Exception as exc:
            run.errors.append(
                {
                    "dlq_id": dlq_id,
                    "detail": f"publish failed: {type(exc).__name__}: {exc}",
                }
            )
            continue
        published.append((dlq_id, record, envelope))

    run.dlq_ids = [dlq_id for dlq_id, _, _ in published]
    run.event_ids = [env.event_id for _, _, env in published]
    run.published_count = len(published)

    # Record the operator's intent against each published DLQ record. Outcome
    # (count, history, resolution) is written by the orchestrator once the
    # event actually resolves, so this never claims a result.
    for dlq_id, _, _ in published:
        try:
            es.update(
                index=DLQ_INDEX,
                id=dlq_id,
                retry_on_conflict=3,
                body={
                    "doc": {
                        "last_reprocess_id": run.reprocess_id,
                        "replay_reason": reason,
                        "last_attempt_at": now,
                    }
                },
            )
        except Exception as exc:
            print(f"Could not stamp replay intent on {dlq_id}: {exc}", flush=True)

    if run.published_count != run.requested_count or run.errors:
        run.status = "partial" if run.published_count else "failed"
        # Persist whenever reality diverged from the optimistic plan --
        # including the partial case. Leaving the stored `published_count`
        # higher than the number of events actually on the topic would make
        # the orchestrator tally wait for outcomes that will never arrive
        # and strand the run in "running" forever.
        if not run.published_count:
            run.completed_at = datetime.now(timezone.utc)
        es.update(
            index=REPROCESS_RUNS_INDEX,
            id=run.reprocess_id,
            retry_on_conflict=3,
            body={"doc": run.model_dump(mode="json")},
        )

    return run


def dry_run_record(es: OpenSearch, dlq_id: str) -> dict:
    """
    Evaluate a replay without writing anything (§30).

    Reads Bronze, runs the real parser chain and reports what WOULD happen.
    Critically this writes no Silver document, does not touch the DLQ record
    and does not publish, so it is safe to run while investigating a failure.
    """
    record = _fetch(es, DLQ_INDEX, dlq_id)
    if record is None:
        raise LookupError(f"dlq record not found: {dlq_id}")

    raw_event_id = record.get("raw_event_id")
    bronze = _fetch(es, BRONZE_INDEX, raw_event_id) if raw_event_id else None
    if bronze is None:
        raise LookupError(f"bronze event not found: {raw_event_id}")

    envelope = envelope_from_bronze(bronze)
    normalized, parsers_attempted = _load_pipeline().normalize_raw_event(envelope)

    # The Drain3 tier is the one verdict a dry run cannot make honestly.
    # Its templates are learned from the live stream inside the ORCHESTRATOR
    # process; the API has its own separate, much colder miner. So an event
    # the API's miner cannot label may still be recovered by a real replay
    # against the orchestrator's warm miner. Reporting a flat "no" here would
    # tell the operator a recoverable record is a dead end.
    drain3_dependent = normalized is None and bool(parsers_attempted) and (
        parsers_attempted[-1] == DRAIN3_PARSER_ID
    )

    return {
        "dlq_id": dlq_id,
        "raw_event_id": raw_event_id,
        "dry_run": True,
        "would_succeed": normalized is not None,
        "drain3_dependent": drain3_dependent,
        "note": (
            "Drain3 fallback reached and it could not label this event using "
            "the templates this process knows. The orchestrator's live miner "
            "has learned from the stream since, so a real replay may still "
            "recover it -- only an actual replay is conclusive here."
            if drain3_dependent
            else None
        ),
        "parsers_attempted": parsers_attempted,
        "previous_parsers_attempted": record.get("parsers_attempted", []),
        "normalized_preview": (
            normalized.model_dump(mode="json") if normalized is not None else None
        ),
    }


# Painless, rather than a read-modify-write, so that N concurrent replays of
# the same record cannot lose an increment or clobber each other's history.
_TALLY_RUN = {
    "lang": "painless",
    "source": (
        "if (params.outcome == 'recovered') { ctx._source.recovered_count = "
        "(ctx._source.recovered_count == null ? 0 : ctx._source.recovered_count) + 1; } "
        "else { ctx._source.failed_count = "
        "(ctx._source.failed_count == null ? 0 : ctx._source.failed_count) + 1; } "
        "int done = (ctx._source.recovered_count == null ? 0 : ctx._source.recovered_count) "
        "+ (ctx._source.failed_count == null ? 0 : ctx._source.failed_count); "
        "int target = ctx._source.published_count == null ? 0 : ctx._source.published_count; "
        # Three distinct terminal states, not two. `partial` means "some
        # recovered, some did not", so a run where nothing recovered is
        # `failed`. Folding those two together reported a total failure as a
        # partial success, and the UI renders this status verbatim -- so an
        # operator replaying one record that failed again was told part of the
        # work landed. `completed` stays reserved for a clean sweep.
        "if (done >= target) { ctx._source.completed_at = params.now; "
        "int rec = ctx._source.recovered_count == null ? 0 : ctx._source.recovered_count; "
        "int fail = ctx._source.failed_count == null ? 0 : ctx._source.failed_count; "
        "ctx._source.status = fail == 0 ? 'completed' : (rec == 0 ? 'failed' : 'partial'); "
        "} else { ctx._source.status = 'running'; }"
    ),
}

_RECORD_ATTEMPT = {
    "lang": "painless",
    "source": (
        # `.add(...)`, not `list + [entry]`: Painless rejects `+` between two
        # ArrayLists with "Cannot apply [+] operation to types
        # [java.util.ArrayList] and [java.util.ArrayList]".
        "if (ctx._source.attempt_history == null) { ctx._source.attempt_history = []; } "
        # Derive the attempt number from the record's own counter, after the
        # increment, so it stays correct across separate runs. Deriving it from
        # the run's counters made every replay of the same record restart at 1.
        "ctx._source.reprocess_count = "
        "(ctx._source.reprocess_count == null ? 0 : ctx._source.reprocess_count) + 1; "
        "params.entry.attempt = ctx._source.reprocess_count; "
        "ctx._source.attempt_history.add(params.entry); "
        "ctx._source.last_attempt_at = params.entry.started_at; "
        "ctx._source.last_reprocess_id = params.entry.reprocess_id; "
        "if (params.result == 'recovered') { ctx._source.resolution_status = 'recovered'; "
        "ctx._source.resolved_at = params.entry.started_at; } "
        # A later failure must clear an earlier recovery, otherwise a record
        # that regressed back to broken would still read as resolved.
        "else { ctx._source.resolution_status = 'unresolved'; "
        "ctx._source.resolved_at = null; }"
    ),
}


def apply_attempt_outcome(
    es: OpenSearch,
    dlq_id: str,
    reprocess_id: Optional[str],
    result: str,
    reason: Optional[str],
) -> None:
    """
    Record a resolved replay attempt against its DLQ record and run.

    `result` is "recovered" or "failed". The DLQ document is updated in
    place -- never deleted (§11) -- so the record keeps its original failure
    classification while `resolution_status` carries the recovery.
    """
    entry = {
        # The script overwrites this from the record's own reprocess_count.
        "attempt": 0,
        "reprocess_id": reprocess_id,
        "started_at": _now(),
        "result": result,
        "reason": reason,
    }

    if reprocess_id:
        # DLQ first: the run tally is bookkeeping, the DLQ history is the
        # audit record, so it must not be lost if the run doc is missing.
        es.update(
            index=DLQ_INDEX,
            id=dlq_id,
            retry_on_conflict=3,
            body={"script": {**_RECORD_ATTEMPT, "params": {"entry": entry, "result": result}}},
        )

        es.update(
            index=REPROCESS_RUNS_INDEX,
            id=reprocess_id,
            retry_on_conflict=3,
            body={
                "script": {
                    **_TALLY_RUN,
                    "params": {"outcome": result, "now": entry["started_at"]},
                }
            },
        )
    else:
        # A replay that lost its run document (e.g. the run was deleted) must
        # still leave an accurate audit trail on the DLQ record itself.
        es.update(
            index=DLQ_INDEX,
            id=dlq_id,
            retry_on_conflict=3,
            body={"script": {**_RECORD_ATTEMPT, "params": {"entry": entry, "result": result}}},
        )
