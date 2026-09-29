"""
Module 3 -- DLQ reprocessing tests.

Covers the mandatory scenarios from the Module 3 handoff:
  Test 1  single successful recovery
  Test 2  replay that still fails
  Test 3  Bronze lineage (replay uses Bronze, not the DLQ payload copy)
  Test 4  parser attempts still recorded
  Test 6  idempotency (no duplicate DLQ rows, deterministic Silver id)

LIMITATION, stated plainly: the outcome counters are written to OpenSearch
with Painless scripts, and a Python fake cannot execute Painless. The fake
below emulates the two scripts' semantics so the Python-side wiring is
covered, but only the live-stack check proves the scripts themselves are
valid. `test_dlq_reprocessing_live.py` is that check.
"""

import asyncio
import copy

import pytest

import orchestrator.dlq.reprocess as reprocess
from db import with_audit_defaults
from orchestrator.parsers.cef_parser import parse_cef_log
from orchestrator.parsers.json_parser import parse_json_log
from orchestrator.parsers.syslog_parser import parse_syslog
from schema.dlq_record import DLQRecord


# A CEF line the real chain parses, used for the "was fixed, now succeeds"
# scenario.
GOOD_CEF = (
    "CEF:0|Fortinet|FortiGate|7.2.0|100|allowed|5|"
    "src=10.0.0.5 dst=10.0.0.9 spt=54321 dpt=443 proto=tcp act=allow"
)
# An unparseable line: no known parser can make a valid event out of it.
BAD_LINE = "%%%% not a log at all &&& ###"

BRONZE_INDEX = "ulpf-bronze"
DLQ_INDEX = "ulpf-dlq"
SILVER_INDEX = "ulpf-silver"
RUNS_INDEX = "ulpf-reprocess-runs"


class FakeES:
    """
    Dict-backed OpenSearch fake.

    `update` understands the two shapes the reprocess service uses: a plain
    `doc` merge, and a `script` whose mutation is emulated from its `params`.
    Emulating by parameter (rather than by pattern-matching the script text)
    keeps these tests coupled to the caller's intent, which is what the
    caller is responsible for.
    """

    def __init__(self, **indices):
        self.data = {name: {} for name in indices}
        for name, docs in indices.items():
            self.data[name] = copy.deepcopy(docs)

    # -- reads ------------------------------------------------------
    def exists(self, index, id=None, **kwargs):
        if id is not None:
            return id in self.data.get(index, {})
        return index in self.data

    def get(self, index, id, **kwargs):
        return {"_source": copy.deepcopy(self.data[index][id])}

    def search(self, index, body=None, **kwargs):
        docs = list(self.data.get(index, {}).values())
        return {
            "hits": {"total": {"value": len(docs)}, "hits": [{"_source": d} for d in docs]}
        }

    # -- writes -----------------------------------------------------
    def index(self, index, id=None, body=None, **kwargs):
        self.data.setdefault(index, {})[id] = copy.deepcopy(body)
        return {"_id": id}

    def update(self, index, id, body=None, retry_on_conflict=None, **kwargs):
        doc = self.data[index][id]
        if "doc" in body:
            doc.update(copy.deepcopy(body["doc"]))
            return {"_id": id}

        params = body["script"]["params"]
        if "entry" in params:
            # _RECORD_ATTEMPT. The attempt number is stamped from the
            # record's own counter, mirroring the Painless script.
            entry = copy.deepcopy(params["entry"])
            doc["reprocess_count"] = doc.get("reprocess_count", 0) + 1
            entry["attempt"] = doc["reprocess_count"]
            doc.setdefault("attempt_history", []).append(entry)
            doc["last_attempt_at"] = entry["started_at"]
            doc["last_reprocess_id"] = entry["reprocess_id"]
            if params["result"] == "recovered":
                doc["resolution_status"] = "recovered"
                doc["resolved_at"] = entry["started_at"]
            else:
                doc["resolution_status"] = "unresolved"
                doc["resolved_at"] = None
            return {"_id": id}

        if "outcome" in params:
            # _TALLY_RUN
            if params["outcome"] == "recovered":
                doc["recovered_count"] = doc.get("recovered_count", 0) + 1
            else:
                doc["failed_count"] = doc.get("failed_count", 0) + 1
            done = doc.get("recovered_count", 0) + doc.get("failed_count", 0)
            target = doc.get("published_count", 0)
            if done >= target:
                doc["completed_at"] = params["now"]
                # Mirrors _TALLY_RUN's three terminal states exactly. A run
                # where nothing recovered is `failed`, not `partial` -- keep
                # this in step with the script or the suite stops describing
                # what production actually does.
                recovered = doc.get("recovered_count", 0)
                failed = doc.get("failed_count", 0)
                if failed == 0:
                    doc["status"] = "completed"
                elif recovered == 0:
                    doc["status"] = "failed"
                else:
                    doc["status"] = "partial"
            else:
                doc["status"] = "running"
            return {"_id": id}

        raise AssertionError(f"unhandled update body: {body}")


class FakeProducer:
    """Captures republished events instead of writing to Redpanda."""

    def __init__(self):
        self.sent = []

    async def send_and_wait(self, topic, value, headers=None):
        import json

        # Mirror aiokafka's actual contract instead of trusting whatever it
        # is handed. A lenient fake hid a real TypeError ("Expected bytes, got
        # str") that only appeared once replay ran against actual Redpanda.
        for key, header_value in headers or []:
            assert isinstance(key, (bytes, str)), f"header key {key!r} must be str"
            assert isinstance(header_value, bytes), (
                f"header {key!r} value must be bytes for aiokafka, "
                f"got {type(header_value).__name__}"
            )

        self.sent.append(
            {
                "topic": topic,
                "payload": json.loads(value.decode("utf-8")),
                "headers": {
                    (k.decode() if isinstance(k, bytes) else k): v
                    for k, v in (headers or [])
                },
            }
        )
        return True


def make_bronze(event_id, payload, format_hint="cef"):
    return {
        DLQ_INDEX: {},
        BRONZE_INDEX: {
            event_id: {
                "event_id": event_id,
                "ingested_at": "2026-09-27T10:00:00+00:00",
                "source_id": "fw-1",
                "source_type": "network_device",
                "transport": "file",
                "format_hint": format_hint,
                "raw_payload": payload,
                "collector_id": "file-collector-1",
                "envelope_schema_version": "1.0.0",
                # Additive lineage the envelope schema forbids. A replay must
                # drop it rather than fail validation.
                "upload_id": "upload-9",
            }
        },
        SILVER_INDEX: {},
        RUNS_INDEX: {},
    }


def make_dlq(dlq_id, raw_event_id, payload, **overrides):
    """
    Build a DLQ record through the real model.

    Constructing the fixture by hand let it drift from the document the
    orchestrator actually writes -- it was missing keys such as
    `resolved_at` that model_dump() always emits, so a test asserting
    `record["resolved_at"] is None` failed on a missing key rather than on
    behaviour. Going through DLQRecord keeps the fixture honest.
    """
    fields = dict(
        dlq_id=dlq_id,
        raw_event_id=raw_event_id,
        raw_payload=payload,
        parsers_attempted=["cef-parser-v1"],
        status="parse_failure",
        classification="no_parser_match",
        metadata={},
    )
    fields.update(overrides)
    return DLQRecord(**fields).model_dump(mode="json")


def replay(es, producer, dlq_ids, reason=None):
    return asyncio.run(
        reprocess.replay_records(
            producer=producer, es=es, dlq_ids=dlq_ids, reason=reason
        )
    )


# ---------------------------------------------------------------------
# Test 3 -- Bronze lineage
# ---------------------------------------------------------------------


def test_replay_republishes_bronze_payload_not_the_dlq_copy():
    """
    The DLQ's raw_payload copy is allowed to be stale. Replay must go back to
    Bronze, so this asserts the republished payload is Bronze's even when the
    two disagree.
    """
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq("dlq-1", "raw-1", "STALE COPY")
    producer = FakeProducer()

    run = replay(es, producer, ["dlq-1"], reason="parser v2 added missing action field")

    assert len(producer.sent) == 1
    sent = producer.sent[0]
    assert sent["payload"]["raw_payload"] == GOOD_CEF
    assert sent["payload"]["raw_payload"] != "STALE COPY"


def test_replay_preserves_original_event_identity():
    """
    Bronze keys on event_id and Silver on `<event_id>-norm`, so a replay that
    minted a new id would duplicate the event instead of updating it.
    """
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq("dlq-1", "raw-1", GOOD_CEF)
    producer = FakeProducer()

    run = replay(es, producer, ["dlq-1"])

    assert producer.sent[0]["payload"]["event_id"] == "raw-1"
    assert run.event_ids == ["raw-1"]


def test_replay_drops_bronze_only_lineage_keys():
    """`upload_id` exists on the Bronze document but not in the envelope,
    which is extra="forbid". Validation would reject it if it leaked."""
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq("dlq-1", "raw-1", GOOD_CEF)
    producer = FakeProducer()

    replay(es, producer, ["dlq-1"])

    assert "upload_id" not in producer.sent[0]["payload"]


# ---------------------------------------------------------------------
# Replay goes through the real pipeline, not a side door
# ---------------------------------------------------------------------


def test_replay_publishes_to_raw_topic_with_replay_headers():
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq("dlq-1", "raw-1", GOOD_CEF)
    producer = FakeProducer()

    run = replay(es, producer, ["dlq-1"], reason="corrected field mapping")

    sent = producer.sent[0]
    # logs.raw, not a private replay topic: the orchestrator must not know
    # that this event is special.
    assert sent["topic"] == "logs.raw"
    # Kafka headers arrive as bytes; assert on the decoded text the
    # orchestrator will compare against.
    assert sent["headers"]["reprocess_of"] == b"dlq-1"
    assert sent["headers"]["reprocess_id"] == run.reprocess_id.encode("utf-8")
    # The operator's reason is captured for audit.
    assert es.data[DLQ_INDEX]["dlq-1"]["replay_reason"] == "corrected field mapping"


def test_run_is_recorded_before_results_are_known():
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq("dlq-1", "raw-1", GOOD_CEF)

    run = replay(es, FakeProducer(), ["dlq-1"])

    stored = es.data[RUNS_INDEX][run.reprocess_id]
    assert stored["status"] == "running"
    assert stored["published_count"] == 1
    assert stored["recovered_count"] == 0
    assert stored["failed_count"] == 0


# ---------------------------------------------------------------------
# Test 1 -- single successful recovery
# ---------------------------------------------------------------------


def test_successful_replay_marks_dlq_recovered_and_keeps_history():
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq("dlq-1", "raw-1", GOOD_CEF)
    run = replay(es, FakeProducer(), ["dlq-1"])

    reprocess.apply_attempt_outcome(
        es=es,
        dlq_id="dlq-1",
        reprocess_id=run.reprocess_id,
        result="recovered",
        reason="replayed via cef-parser-v1",
    )

    record = es.data[DLQ_INDEX]["dlq-1"]
    assert record["resolution_status"] == "recovered"
    assert record["resolved_at"] is not None
    assert record["reprocess_count"] == 1
    assert len(record["attempt_history"]) == 1
    assert record["attempt_history"][0]["result"] == "recovered"

    stored = es.data[RUNS_INDEX][run.reprocess_id]
    assert stored["recovered_count"] == 1
    assert stored["status"] == "completed"
    assert stored["completed_at"] is not None


def test_recovery_never_erases_the_original_failure_reason():
    """
    §11/§12: the DLQ is an audit trail. "Was this a failure?" must stay
    answerable, so `status` keeps its original classification.
    """
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq("dlq-1", "raw-1", GOOD_CEF)
    run = replay(es, FakeProducer(), ["dlq-1"])

    reprocess.apply_attempt_outcome(
        es=es, dlq_id="dlq-1", reprocess_id=run.reprocess_id, result="recovered", reason=None
    )

    record = es.data[DLQ_INDEX]["dlq-1"]
    assert record["status"] == "parse_failure"
    assert record["classification"] == "no_parser_match"
    assert record["resolution_status"] == "recovered"


# ---------------------------------------------------------------------
# Test 2 -- replay that fails again
# ---------------------------------------------------------------------


def test_failed_replay_stays_unresolved_and_increments_count():
    es = FakeES(**make_bronze("raw-2", BAD_LINE))
    es.data[DLQ_INDEX]["dlq-2"] = make_dlq(
        "dlq-2", "raw-2", BAD_LINE, resolution_status="unresolved", reprocess_count=1
    )
    run = replay(es, FakeProducer(), ["dlq-2"])

    reprocess.apply_attempt_outcome(
        es=es, dlq_id="dlq-2", reprocess_id=run.reprocess_id, result="failed", reason="no parser"
    )

    record = es.data[DLQ_INDEX]["dlq-2"]
    assert record["resolution_status"] == "unresolved"
    assert record["resolved_at"] is None
    assert record["reprocess_count"] == 2
    assert record["attempt_history"][-1]["result"] == "failed"

    stored = es.data[RUNS_INDEX][run.reprocess_id]
    assert stored["failed_count"] == 1
    assert stored["recovered_count"] == 0
    # A run where everything failed is 'failed', not 'partial': 'partial' is
    # reserved for a genuine mix, and 'completed' would claim a clean sweep.
    assert stored["status"] == "failed"


def test_failed_replay_creates_no_silver_document():
    es = FakeES(**make_bronze("raw-2", BAD_LINE))
    es.data[DLQ_INDEX]["dlq-2"] = make_dlq("dlq-2", "raw-2", BAD_LINE)
    run = replay(es, FakeProducer(), ["dlq-2"])

    reprocess.apply_attempt_outcome(
        es=es, dlq_id="dlq-2", reprocess_id=run.reprocess_id, result="failed", reason=None
    )

    # Nothing in the replay path may write Silver -- only the orchestrator
    # does, and only on a real parse success.
    assert es.data[SILVER_INDEX] == {}


# ---------------------------------------------------------------------
# Test 6 -- idempotency
# ---------------------------------------------------------------------


def test_repeated_replays_never_create_a_second_dlq_record():
    """
    The DLQ index is never appended to by a replay. A second failing replay
    updates the same document; otherwise one unparseable event would produce
    one DLQ row per attempt.
    """
    es = FakeES(**make_bronze("raw-2", BAD_LINE))
    es.data[DLQ_INDEX]["dlq-2"] = make_dlq("dlq-2", "raw-2", BAD_LINE)

    for _ in range(3):
        run = replay(es, FakeProducer(), ["dlq-2"])
        reprocess.apply_attempt_outcome(
            es=es, dlq_id="dlq-2", reprocess_id=run.reprocess_id, result="failed", reason=None
        )

    assert len(es.data[DLQ_INDEX]) == 1
    record = es.data[DLQ_INDEX]["dlq-2"]
    assert record["reprocess_count"] == 3
    assert len(record["attempt_history"]) == 3


def test_attempt_history_appends_rather_than_overwrites():
    es = FakeES(**make_bronze("raw-2", BAD_LINE))
    es.data[DLQ_INDEX]["dlq-2"] = make_dlq("dlq-2", "raw-2", BAD_LINE)

    first = replay(es, FakeProducer(), ["dlq-2"])
    reprocess.apply_attempt_outcome(
        es=es, dlq_id="dlq-2", reprocess_id=first.reprocess_id, result="failed", reason="first"
    )
    second = replay(es, FakeProducer(), ["dlq-2"])
    reprocess.apply_attempt_outcome(
        es=es, dlq_id="dlq-2", reprocess_id=second.reprocess_id, result="recovered", reason="fixed"
    )

    history = es.data[DLQ_INDEX]["dlq-2"]["attempt_history"]
    assert [h["result"] for h in history] == ["failed", "recovered"]
    assert [h["reprocess_id"] for h in history] == [
        first.reprocess_id,
        second.reprocess_id,
    ]


@pytest.mark.parametrize("parser", [parse_cef_log, parse_json_log, parse_syslog])
def test_silver_id_is_deterministic_per_parser(parser):
    """
    Every parser derives `<raw_event_id>-norm`, which is what makes a replay
    overwrite Silver ("latest state") instead of appending a duplicate.
    """
    payload = {
        parse_cef_log: GOOD_CEF,
        parse_json_log: '{"user":"erin","time":"2026-09-27T10:00:00Z"}',
        parse_syslog: "<34>Sep 27 10:00:00 host-1 sshd: hello",
    }[parser]

    first = parser(payload, raw_event_id="raw-1", source_id="fw-1")
    second = parser(payload, raw_event_id="raw-1", source_id="fw-1")

    assert first.event_id == "raw-1-norm"
    assert first.event_id == second.event_id
    assert first.raw_event_id == "raw-1"


# ---------------------------------------------------------------------
# Test 4 -- parser attempts still recorded
# ---------------------------------------------------------------------


def test_dlq_record_carries_parsers_attempted_through_replay():
    es = FakeES(**make_bronze("raw-2", BAD_LINE))
    es.data[DLQ_INDEX]["dlq-2"] = make_dlq(
        "dlq-2",
        "raw-2",
        BAD_LINE,
        parsers_attempted=["cef-parser-v1", "json-parser-v1", "drain3-fallback-v1"],
    )

    replay(es, FakeProducer(), ["dlq-2"])

    record = es.data[DLQ_INDEX]["dlq-2"]
    assert record["parsers_attempted"] == [
        "cef-parser-v1",
        "json-parser-v1",
        "drain3-fallback-v1",
    ]


# ---------------------------------------------------------------------
# Batch behaviour
# ---------------------------------------------------------------------


def test_batch_replay_reports_per_record_errors_and_publishes_the_rest():
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq("dlq-1", "raw-1", GOOD_CEF)
    producer = FakeProducer()

    run = replay(es, producer, ["dlq-1", "dlq-missing", "dlq-no-bronze"])

    # Two of the three are unresolvable: one has no record, one points at a
    # Bronze document that is gone.
    es.data[DLQ_INDEX]["dlq-no-bronze"] = make_dlq("dlq-no-bronze", "raw-gone", "x")
    run = replay(es, producer, ["dlq-1", "dlq-missing", "dlq-no-bronze"])

    assert run.requested_count == 3
    assert run.published_count == 1
    assert run.dlq_ids == ["dlq-1"]
    assert {e["dlq_id"] for e in run.errors} == {"dlq-missing", "dlq-no-bronze"}
    assert run.status == "partial"


def test_batch_where_nothing_is_replayable_fails_fast():
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    producer = FakeProducer()

    run = replay(es, producer, ["nope-1", "nope-2"])

    assert run.published_count == 0
    assert run.status == "failed"
    assert producer.sent == []


def test_replay_without_bronze_refuses_to_fall_back_to_dlq_payload():
    """
    A missing Bronze document must be an error, not a silent replay of the
    DLQ's copied payload -- that fallback would break the lineage guarantee.
    """
    es = FakeES()
    es.data[DLQ_INDEX] = {"dlq-1": make_dlq("dlq-1", "raw-gone", GOOD_CEF)}
    es.data[BRONZE_INDEX] = {}
    producer = FakeProducer()

    run = replay(es, producer, ["dlq-1"])

    assert producer.sent == []
    assert run.errors[0]["detail"].startswith("bronze event not found")


# ---------------------------------------------------------------------
# Dry run (§30)
# ---------------------------------------------------------------------


def test_dry_run_reports_would_succeed_without_writing_anything(monkeypatch):
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq("dlq-1", "raw-1", GOOD_CEF)
    before = copy.deepcopy(es.data)

    result = reprocess.dry_run_record(es=es, dlq_id="dlq-1")

    assert result["dry_run"] is True
    assert result["would_succeed"] is True
    assert result["parsers_attempted"] == ["cef-parser-v1"]
    assert result["normalized_preview"]["parser_id"] == "cef-parser-v1"
    # The whole point of a dry run: nothing changed.
    assert es.data == before


def test_dry_run_reports_failure_for_unparseable_event():
    # format_hint="unknown" is what makes the orchestrator probe the whole
    # chain. A "cef"-hinted event only ever tries the CEF parser and returns,
    # so asserting a Drain3 attempt on it would test the wrong thing.
    es = FakeES(**make_bronze("raw-2", BAD_LINE, format_hint="unknown"))
    es.data[DLQ_INDEX]["dlq-2"] = make_dlq("dlq-2", "raw-2", BAD_LINE)

    result = reprocess.dry_run_record(es=es, dlq_id="dlq-2")

    assert result["would_succeed"] is False
    assert result["normalized_preview"] is None
    # It must have walked the whole chain, Drain3 fallback included.
    assert "drain3-fallback-v1" in result["parsers_attempted"]


def test_dry_run_of_hinted_format_only_walks_that_parser():
    """
    Documents real chain behaviour instead of assuming it: a cef hint commits
    to the CEF parser and does not fall through to the others.
    """
    es = FakeES(**make_bronze("raw-1", GOOD_CEF, format_hint="cef"))
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq("dlq-1", "raw-1", GOOD_CEF)

    result = reprocess.dry_run_record(es=es, dlq_id="dlq-1")

    assert result["would_succeed"] is True
    assert result["parsers_attempted"] == ["cef-parser-v1"]


def test_dry_run_raises_for_unknown_record():
    es = FakeES()
    with pytest.raises(LookupError):
        reprocess.dry_run_record(es=es, dlq_id="ghost")


# ---------------------------------------------------------------------
# Backwards compatibility with pre-Module-3 records
# ---------------------------------------------------------------------


def test_legacy_dlq_record_reads_as_unresolved():
    legacy = {
        "dlq_id": "d",
        "raw_event_id": "r",
        "parsers_attempted": ["cef-parser-v1"],
        "status": "parse_failure",
    }
    filled = with_audit_defaults(legacy)

    assert filled["resolution_status"] == "unresolved"
    assert filled["reprocess_count"] == 0
    assert filled["attempt_history"] == []
    assert filled["resolved_at"] is None


def test_dlq_record_model_defaults_to_unresolved():
    record = DLQRecord(
        dlq_id="d",
        raw_event_id="r",
        parsers_attempted=["cef-parser-v1"],
        status="parse_failure",
    )
    assert record.resolution_status == "unresolved"
    assert record.reprocess_count == 0
    assert record.attempt_history == []


def test_dlq_record_still_rejects_unknown_fields():
    """The recovery fields must not turn the envelope/record schemas into
    open bags -- `extra=forbid` is what keeps that guarantee."""
    with pytest.raises(Exception):
        DLQRecord(
            dlq_id="d",
            raw_event_id="r",
            parsers_attempted=[],
            status="parse_failure",
            something_unexpected="x",
        )
