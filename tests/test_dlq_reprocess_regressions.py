"""
Regression tests for the defects found while verifying Module 3 live.

Each of these passed the original fake-based suite and failed only against a
real stack, or was a logic gap the fakes were structurally unable to catch:

  1. A batch where SOME Kafka publishes fail kept the optimistic
     `published_count` in OpenSearch, so the run waited for outcomes that
     could never arrive and was stranded in "running" forever.
  2. Replay intent (`last_reprocess_id`, `replay_reason`) was stamped on
     records whose publish had failed, claiming an attempt never made.
  3. Duplicate ids in one request were replayed once per occurrence.
  4. The Drain3 fallback tier was unreachable for any format hint other
     than exactly "unknown" -- "auto", "raw", "text", "leef" and None all
     dead-ended to the DLQ without Drain3 ever being tried.
  5. A dry run reported a flat "would not recover" for events whose real
     outcome depends on the orchestrator's live Drain3 miner, so operators
     were told recoverable records were dead ends.
"""

import pytest

from orchestrator.dlq import reprocess as reprocess_mod
from tests.test_dlq_reprocessing import (  # noqa: F401
    BRONZE_INDEX,
    DLQ_INDEX,
    GOOD_CEF,
    RUNS_INDEX,
    FakeES,
    make_bronze,
    make_dlq,
    replay,
)

# GOOD_CEF is imported rather than retyped: a hand-copied CEF string is
# missing a field often enough to silently stop being parseable.


class FlakyProducer:
    """Fails the publish for ids whose payload marker is 'POISON'."""

    def __init__(self):
        self.sent = []

    async def send_and_wait(self, topic, value, headers=None):
        import json

        payload = json.loads(value.decode("utf-8"))
        if "POISON" in payload["raw_payload"]:
            raise RuntimeError("broker unavailable")

        for key, header_value in headers or []:
            assert isinstance(header_value, bytes)

        self.sent.append(
            {
                "topic": topic,
                "payload": payload,
                "headers": {
                    (k.decode() if isinstance(k, bytes) else k): v
                    for k, v in (headers or [])
                },
            }
        )
        return True


def _seed(es, n=3):
    """Seed n dlq/bronze pairs; every third event is unpublishable."""
    for i in range(1, n + 1):
        payload = f"{GOOD_CEF} POISON" if i % 3 == 0 else GOOD_CEF
        hint = "cef" if i % 3 == 0 else "unknown"
        es.data[BRONZE_INDEX][f"raw-{i}"] = make_bronze(f"raw-{i}", payload, hint)[
            BRONZE_INDEX
        ][f"raw-{i}"]
        es.data[DLQ_INDEX][f"dlq-{i}"] = make_dlq(f"dlq-{i}", f"raw-{i}", payload)


# ---------------------------------------------------------------- publish bugs


def test_partial_publish_failure_persists_the_real_published_count():
    """
    The stored run must reflect what actually reached the topic. Leaving the
    optimistic count in place strands the run in "running" forever.
    """
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    _seed(es, 3)
    producer = FlakyProducer()

    run = replay(es, producer, ["dlq-1", "dlq-2", "dlq-3"])

    assert run.status == "partial"
    assert run.published_count == 2, "only the two non-poison events published"
    assert run.requested_count == 3

    # The persisted document, not just the returned object.
    stored = es.data[RUNS_INDEX][run.reprocess_id]
    assert stored["published_count"] == 2
    assert stored["status"] == "partial"
    assert stored["dlq_ids"] == ["dlq-1", "dlq-2"]
    assert stored["errors"], "the failure must be explained in the run document"

    # The tally target is now reachable, so the run can complete.
    for _ in range(2):
        reprocess_mod.apply_attempt_outcome(
            es=es,
            dlq_id=stored["dlq_ids"][0],
            reprocess_id=run.reprocess_id,
            result="recovered",
            reason=None,
        )
    assert es.data[RUNS_INDEX][run.reprocess_id]["status"] == "completed"


def test_failed_publish_does_not_stamp_replay_intent_on_the_dlq_record():
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    _seed(es, 3)
    producer = FlakyProducer()

    run = replay(es, producer, ["dlq-1", "dlq-2", "dlq-3"], reason="batch run")

    poisoned = es.data[DLQ_INDEX]["dlq-3"]
    assert poisoned.get("last_reprocess_id") is None, (
        "a record whose publish failed must not claim a replay attempt"
    )
    assert poisoned.get("replay_reason") is None

    published = es.data[DLQ_INDEX]["dlq-1"]
    assert published["last_reprocess_id"] == run.reprocess_id
    assert published["replay_reason"] == "batch run"


def test_all_publishes_failing_marks_the_run_failed_and_closed():
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    es.data[BRONZE_INDEX]["raw-9"] = make_bronze("raw-9", f"{GOOD_CEF} POISON")[
        BRONZE_INDEX
    ]["raw-9"]
    es.data[DLQ_INDEX]["dlq-9"] = make_dlq("dlq-9", "raw-9", f"{GOOD_CEF} POISON")

    run = replay(es, FlakyProducer(), ["dlq-9"])

    assert run.published_count == 0
    assert run.status == "failed"
    stored = es.data[RUNS_INDEX][run.reprocess_id]
    assert stored["status"] == "failed"
    assert stored["completed_at"] is not None
    assert es.data[DLQ_INDEX]["dlq-9"].get("last_reprocess_id") is None


# ------------------------------------------------------------------ dedupe bug


def test_duplicate_ids_are_replayed_once():
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    es.data[BRONZE_INDEX]["raw-1"] = make_bronze("raw-1", GOOD_CEF)[BRONZE_INDEX][
        "raw-1"
    ]
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq("dlq-1", "raw-1", GOOD_CEF)
    producer = FlakyProducer()

    run = replay(es, producer, ["dlq-1", "dlq-1", "dlq-1"])

    assert len(producer.sent) == 1, "must publish once, not three times"
    assert run.requested_count == 1, "requested_count counts distinct records"
    assert run.published_count == 1
    assert run.dlq_ids == ["dlq-1"]
    assert any("duplicate" in e["detail"] for e in run.errors)


def test_dedupe_preserves_order_and_keeps_distinct_ids():
    es = FakeES(**make_bronze("raw-1", GOOD_CEF))
    _seed(es, 3)
    producer = FlakyProducer()

    run = replay(es, producer, ["dlq-3", "dlq-1", "dlq-3", "dlq-2"])

    assert run.requested_count == 3
    # dlq-3 is poison, so the surviving publish order is dlq-1 then dlq-2.
    assert run.dlq_ids == ["dlq-1", "dlq-2"]


# ------------------------------------------------------------------ drain3 tier


@pytest.mark.parametrize("hint", ["unknown", "auto", "raw", "text", "leef", "", None])
def test_every_unrecognised_hint_still_reaches_the_drain3_tier(hint):
    """
    Any hint without a parser of its own must still get Drain3 tried. Before
    the fix only exactly "unknown" reached it; the rest dead-ended to the DLQ.
    """
    from orchestrator.main import normalize_raw_event
    from schema.raw_event import RawEventEnvelope

    # No CEF, no <PRI>, not JSON -> cef/json/syslog all fail, so the only
    # thing that can parse this is the Drain3 fallback.
    payload = "Sep 16 12:00:00 fw-1 action=blocked src=10.0.0.5 dst=8.8.8.8 port=443"
    event = RawEventEnvelope(
        event_id="raw-hint",
        ingested_at="2026-09-27T10:00:00+00:00",
        source_id="fw-1",
        source_type="network_device",
        transport="file",
        format_hint=hint,
        raw_payload=payload,
        collector_id="c1",
        envelope_schema_version="1.0.0",
    )

    normalized, attempted = normalize_raw_event(event)

    assert "drain3-fallback-v1" in attempted, (
        f"hint={hint!r} never tried drain3 (attempted={attempted})"
    )
    assert normalized is not None, f"hint={hint!r} should have parsed via drain3"
    assert normalized.parser_id == "drain3-fallback-v1"
    assert normalized.parser_tier == "drain3"
    # Drain3 output is never high confidence, by policy.
    assert normalized.confidence_score == 0.5
    assert normalized.src_endpoint == "10.0.0.5"


def test_dedicated_hint_still_commits_to_its_own_parser_only():
    """A hint that PARSES must not also drag in the fallback tier."""
    from orchestrator.main import normalize_raw_event
    from schema.raw_event import RawEventEnvelope

    event = RawEventEnvelope(
        event_id="raw-cef",
        ingested_at="2026-09-27T10:00:00+00:00",
        source_id="fw-1",
        source_type="network_device",
        transport="file",
        format_hint="cef",
        raw_payload=GOOD_CEF,
        collector_id="c1",
        envelope_schema_version="1.0.0",
    )

    normalized, attempted = normalize_raw_event(event)

    assert attempted == ["cef-parser-v1"]
    assert "drain3-fallback-v1" not in attempted
    assert normalized.parser_id == "cef-parser-v1"


# A line the dedicated parser for its declared hint will refuse, but which
# Drain3 can still stand an event up on.
_MISLABELLED_BUT_RECOVERABLE = (
    "Sep 16 12:00:00 fw-1 action=blocked src=10.0.0.5 dst=8.8.8.8 port=443"
)


def _raw(hint: str, payload: str):
    from schema.raw_event import RawEventEnvelope

    return RawEventEnvelope(
        event_id="raw-mislabel",
        ingested_at="2026-09-27T10:00:00+00:00",
        source_id="fw-1",
        source_type="network_device",
        transport="file",
        format_hint=hint,
        raw_payload=payload,
        collector_id="c1",
        envelope_schema_version="1.0.0",
    )


@pytest.mark.parametrize("hint", ["cef", "json", "syslog"])
def test_dedicated_hint_falls_through_to_drain3_when_its_own_parser_declines(hint):
    """
    A declared hint is a PREFERENCE, not a commitment that dead-ends in the DLQ.

    When the source says "json" but the line is not JSON, the only thing that
    happens today is `json-parser-v1` is tried, fails, and the event goes
    straight to the DLQ. Drain3 -- the tier that exists precisely for "no
    dedicated parser matched" -- is never given the chance, so a mislabelled
    or drifted line is dropped into the DLQ even though the fallback could
    have produced an event from it.

    Every DLQ record the pipeline mints for this reason is work an operator has
    to triage and replay, when the fallback tier could have handled it inline.
    """
    from orchestrator.main import normalize_raw_event

    normalized, attempted = normalize_raw_event(_raw(hint, _MISLABELLED_BUT_RECOVERABLE))

    assert attempted == [
        {"cef": "cef-parser-v1", "json": "json-parser-v1", "syslog": "syslog-parser-v1"}[hint],
        "drain3-fallback-v1",
    ], f"hint={hint!r} did not fall through to drain3 (attempted={attempted})"
    assert normalized is not None, (
        f"hint={hint!r} went to the DLQ even though drain3 could parse the line"
    )
    assert normalized.parser_id == "drain3-fallback-v1"
    assert normalized.parser_tier == "drain3"
    # Still never high confidence, whatever tier answered.
    assert normalized.confidence_score == 0.5
    assert normalized.src_endpoint == "10.0.0.5"


def test_dedicated_hint_records_that_its_own_parser_ran_first():
    """
    The fall-through must stay auditable: a DLQ record built from a hinted
    event has to show that the declared parser was tried before the fallback,
    or "why did this not parse as JSON" is unanswerable after the fact.
    """
    from orchestrator.dlq.reprocess import HEADER_REPROCESS_ID  # noqa: F401
    from orchestrator.main import create_dlq_record

    record = create_dlq_record(_raw("json", "total noise, no identity at all"), ["json-parser-v1"])

    assert record.parsers_attempted == ["json-parser-v1"]
    assert record.classification == "no_parser_match"


def test_drain3_still_declines_lines_with_no_recoverable_identity():
    """
    Drain3 must not invent an event from pure noise. With no src/dst/user/mac
    label there is nothing to stand an event up on, so it returns None and
    the event goes to the DLQ.
    """
    from orchestrator.main import normalize_raw_event
    from schema.raw_event import RawEventEnvelope

    event = RawEventEnvelope(
        event_id="raw-noise",
        ingested_at="2026-09-27T10:00:00+00:00",
        source_id="fw-1",
        source_type="network_device",
        transport="file",
        format_hint="unknown",
        raw_payload="just some words with nothing identifiable inside",
        collector_id="c1",
        envelope_schema_version="1.0.0",
    )

    normalized, attempted = normalize_raw_event(event)

    assert "drain3-fallback-v1" in attempted, "drain3 must have been tried"
    assert normalized is None


# ------------------------------- naming the residual reject for what it is


def test_exhausted_chain_is_labelled_no_recoverable_identity():
    """
    Once the Drain3 tier has run and declined, "no_parser_match" is a lie: the
    format was matched-or-exhausted and the fallback was tried. The real reason
    is that the line carried no field Drain3 could label as an identity, which
    is a different problem with a different operator response than "we do not
    know what this format is".
    """
    from orchestrator.main import create_dlq_record

    record = create_dlq_record(
        _raw("json", "total noise with no identity anywhere"),
        ["json-parser-v1", "drain3-fallback-v1"],
    )

    assert record.classification == "no_recoverable_identity"
    assert record.status == "parse_failure"
    assert record.metadata["reject_reason"], (
        "the label is only actionable if it says WHY there is nothing to stand "
        "an event on"
    )


def test_exhausted_chain_label_wins_over_format_unidentified():
    """
    hint=unknown with an exhausted chain used to report format_unidentified.
    Reaching the fallback tier means every parser was already tried, so the
    decisive fact is the missing identity, not the unknown hint.
    """
    from orchestrator.main import create_dlq_record

    record = create_dlq_record(
        _raw("unknown", "just some words with nothing identifiable inside"),
        ["cef-parser-v1", "json-parser-v1", "syslog-parser-v1", "drain3-fallback-v1"],
    )

    assert record.classification == "no_recoverable_identity"


def test_exhausted_chain_label_does_not_swallow_an_index_rejection():
    """
    index_rejected means the event DID parse and only OpenSearch refused it.
    That is a different fault entirely and keeps its own status, so the new
    label must not fire for it.
    """
    from orchestrator.main import create_dlq_record

    record = create_dlq_record(
        _raw("json", '{"level": "notice"}'),
        ["json-parser-v1", "drain3-fallback-v1"],
        classification="index_rejected",
        status="index_failure",
    )

    assert record.classification == "index_rejected"
    assert record.status == "index_failure"


def test_labels_without_the_fallback_tier_are_unchanged():
    """
    The new label describes the fallback's own decline. A record that never
    reached that tier keeps the classification it always had, so existing
    history and the dry-run reasoning stay comparable.
    """
    from orchestrator.main import create_dlq_record

    hinted = create_dlq_record(_raw("json", '{"a":'), ["json-parser-v1"])
    assert hinted.classification == "no_parser_match"

    unknown = create_dlq_record(_raw("unknown", "free text"), ["a"])
    assert unknown.classification == "format_unidentified"


def test_the_real_truncated_json_dlq_record_is_labelled_for_its_actual_cause():
    """
    This is the exact payload sitting in the live DLQ. It carries a user and a
    host in plain text but the JSON is cut mid-value, so Drain3's miner sees a
    fixed template with no variables to label. The record has to name that
    cause, because "no_parser_match" sends an operator hunting for a parser
    bug when nothing about the parser is broken.
    """
    from orchestrator.main import create_dlq_record, normalize_raw_event

    event = _raw("json", '{"host": "win-web-03", "user": "erin", "level": ')
    normalized, attempted = normalize_raw_event(event)

    assert normalized is None, "premise: this payload cannot be normalized"
    assert attempted == ["json-parser-v1", "drain3-fallback-v1"]

    record = create_dlq_record(event, attempted)
    assert record.classification == "no_recoverable_identity"


# ----------------------------------------------- dry-run honesty about drain3


def test_dry_run_flags_a_verdict_that_depends_on_the_live_miner():
    """
    A dry run that says "no" is read by an operator as "this is a dead end".
    For the Drain3 tier that is wrong: the API process has its own separate,
    colder miner than the orchestrator, so a real replay can still recover it.
    The dry run must therefore say its verdict is miner-dependent.
    """
    es = FakeES(**make_bronze("raw-1", "just some words with nothing identifiable"))
    es.data[BRONZE_INDEX]["raw-1"] = make_bronze(
        "raw-1", "just some words with nothing identifiable", format_hint="unknown"
    )[BRONZE_INDEX]["raw-1"]
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq(
        "dlq-1", "raw-1", "just some words with nothing identifiable"
    )

    result = reprocess_mod.dry_run_record(es, "dlq-1")

    assert result["would_succeed"] is False
    assert result["drain3_dependent"] is True
    assert "drain3-fallback-v1" in result["parsers_attempted"]
    assert result["note"], "a miner-dependent verdict must carry an explanation"
    assert "replay" in result["note"]


def test_dry_run_makes_a_plain_claim_when_no_miner_is_involved():
    """
    The caveat must stay narrow: a record that failed on a dedicated parser
    is a real, stable failure, so it is reported as a definitive miss.
    """
    payload = (
        "CEF:0|Vendor|Product|1.0|100|allow|5|src=10.0.0.5 dst=10.0.0.9 proto=tcp"
    )
    es = FakeES(**make_bronze("raw-1", payload))
    es.data[BRONZE_INDEX]["raw-1"] = make_bronze("raw-1", payload)[BRONZE_INDEX][
        "raw-1"
    ]
    es.data[DLQ_INDEX]["dlq-1"] = make_dlq("dlq-1", "raw-1", payload)

    result = reprocess_mod.dry_run_record(es, "dlq-1")

    assert result["would_succeed"] is True
    assert result["drain3_dependent"] is False
    assert result["note"] is None
