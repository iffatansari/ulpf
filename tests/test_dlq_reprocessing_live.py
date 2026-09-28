"""
Live integration tests for the Module 3 audit scripts.

`FakeES` in test_dlq_reprocessing.py emulates these scripts in Python, which
means it can never catch a Painless compile or runtime error. Both scripts
below shipped broken and were only found by running them against a real
OpenSearch:

  * header values sent as str -> aiokafka "Expected bytes, got str"
  * `attempt_history + [entry]` -> "Cannot apply [+] operation to types
    [java.util.ArrayList] and [java.util.ArrayList]"

So the scripts are executed for real here. If OpenSearch is not reachable the
tests skip rather than fail, keeping the unit suite runnable without a stack.
"""
import os

import pytest
from opensearchpy import OpenSearch

os.environ.setdefault("OPENSEARCH_URL", "http://localhost:9200")

from orchestrator.dlq.reprocess import (  # noqa: E402
    _RECORD_ATTEMPT,
    _TALLY_RUN,
    _now,
)

PROBE_INDEX = "m3-script-probe"


def _es():
    try:
        client = OpenSearch(
            [os.environ["OPENSEARCH_URL"]], timeout=5, max_retries=1, retry_on_timeout=False
        )
        if not client.ping():
            pytest.skip("OpenSearch not reachable")
        return client
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"OpenSearch not reachable: {exc}")


@pytest.fixture
def es():
    client = _es()
    try:
        client.indices.delete(index=PROBE_INDEX)
    except Exception:
        pass
    client.indices.create(
        index=PROBE_INDEX,
        body={
            "mappings": {
                "properties": {
                    "attempt_history": {"type": "object", "enabled": False},
                    "reprocess_count": {"type": "integer"},
                    "published_count": {"type": "integer"},
                    "recovered_count": {"type": "integer"},
                    "failed_count": {"type": "integer"},
                }
            }
        },
    )
    yield client
    try:
        client.indices.delete(index=PROBE_INDEX)
    except Exception:
        pass


def _entry(result="failed", attempt=1):
    return {
        "attempt": attempt,
        "reprocess_id": "run-1",
        "started_at": _now(),
        "result": result,
        "reason": "no parser matched",
    }


def test_record_attempt_script_compiles_and_increments(es):
    es.index(index=PROBE_INDEX, id="doc-1", body={"reprocess_count": 0})

    es.update(
        index=PROBE_INDEX,
        id="doc-1",
        retry_on_conflict=3,
        body={
            "script": {
                **_RECORD_ATTEMPT,
                "params": {"entry": _entry(), "result": "failed"},
            }
        },
    )

    doc = es.get(index=PROBE_INDEX, id="doc-1")["_source"]
    assert doc["reprocess_count"] == 1
    assert len(doc["attempt_history"]) == 1
    assert doc["attempt_history"][0]["result"] == "failed"
    assert doc["last_reprocess_id"] == "run-1"


def test_record_attempt_appends_rather_than_overwrites_history(es):
    es.index(index=PROBE_INDEX, id="doc-1", body={"reprocess_count": 0})

    for attempt in (1, 2, 3):
        es.update(
            index=PROBE_INDEX,
            id="doc-1",
            retry_on_conflict=3,
            body={
                "script": {
                    **_RECORD_ATTEMPT,
                    "params": {
                        "entry": _entry(attempt=attempt),
                        "result": "failed",
                    },
                }
            },
        )

    doc = es.get(index=PROBE_INDEX, id="doc-1")["_source"]
    assert [e["attempt"] for e in doc["attempt_history"]] == [1, 2, 3]
    assert doc["reprocess_count"] == 3

def test_record_attempt_recovery_then_regression_clears_resolved(es):
    """A record that recovered and then broke again must read unresolved."""
    es.index(index=PROBE_INDEX, id="doc-1", body={"reprocess_count": 0})

    for result in ("recovered", "failed"):
        es.update(
            index=PROBE_INDEX,
            id="doc-1",
            retry_on_conflict=3,
            body={
                "script": {
                    **_RECORD_ATTEMPT,
                    "params": {"entry": _entry(result=result), "result": result},
                }
            },
        )
        doc = es.get(index=PROBE_INDEX, id="doc-1")["_source"]
        if result == "recovered":
            assert doc["resolution_status"] == "recovered"
            assert doc["resolved_at"] is not None
        else:
            assert doc["resolution_status"] == "unresolved"
            assert doc["resolved_at"] is None

    # History is never lost, and the original first_seen metadata survives.
    assert len(doc["attempt_history"]) == 2


def test_tally_run_completes_when_every_published_event_reports(es):
    es.index(index=PROBE_INDEX, id="run-1", body={"published_count": 2})

    es.update(
        index=PROBE_INDEX,
        id="run-1",
        retry_on_conflict=3,
        body={"script": {**_TALLY_RUN, "params": {"outcome": "recovered", "now": _now()}}},
    )
    assert es.get(index=PROBE_INDEX, id="run-1")["_source"]["status"] == "running"

    es.update(
        index=PROBE_INDEX,
        id="run-1",
        retry_on_conflict=3,
        body={"script": {**_TALLY_RUN, "params": {"outcome": "failed", "now": _now()}}},
    )
    doc = es.get(index=PROBE_INDEX, id="run-1")["_source"]
    assert doc["recovered_count"] == 1
    assert doc["failed_count"] == 1
    # Some recovered, some failed -> partial, and the run is closed out.
    assert doc["status"] == "partial"
    assert doc["completed_at"] is not None


def _tally(es, run_id, outcome):
    es.update(
        index=PROBE_INDEX,
        id=run_id,
        retry_on_conflict=3,
        body={"script": {**_TALLY_RUN, "params": {"outcome": outcome, "now": _now()}}},
    )
    return es.get(index=PROBE_INDEX, id=run_id)["_source"]


def test_tally_run_reports_failed_when_nothing_recovered(es):
    """
    A run in which every replay failed again is `failed`, not `partial`.

    `partial` means "some recovered, some did not". Reporting it when
    recovered_count is 0 tells the operator that part of their replay worked
    when none of it did -- and the UI now renders this status verbatim, so the
    operator would read a total failure as a partial success. The most common
    case is the single-record replay that fails again, which is exactly the
    case `partial` mislabels.
    """
    es.index(index=PROBE_INDEX, id="run-all-failed", body={"published_count": 1})

    doc = _tally(es, "run-all-failed", "failed")

    assert doc["failed_count"] == 1
    assert doc.get("recovered_count", 0) == 0
    assert doc["status"] == "failed"
    # Terminal, so the UI's poller stops instead of waiting forever.
    assert doc["completed_at"] is not None


def test_tally_run_reports_failed_for_a_multi_event_all_failed_run(es):
    """
    Same rule at batch scale, so a 1000-record run that recovers nothing is
    never mistaken for a partial success.
    """
    es.index(index=PROBE_INDEX, id="run-all-failed-3", body={"published_count": 3})

    for _ in range(3):
        doc = _tally(es, "run-all-failed-3", "failed")

    assert doc["failed_count"] == 3
    assert doc["status"] == "failed"


def test_tally_run_stays_partial_when_only_some_recover(es):
    """
    The `partial` case is preserved: a genuine mix is still `partial`.
    Guards against over-correcting the fix above into a blunt "any failure is
    failed", which would hide partial recoveries from the operator.
    """
    es.index(index=PROBE_INDEX, id="run-mixed", body={"published_count": 3})

    _tally(es, "run-mixed", "recovered")
    doc = _tally(es, "run-mixed", "failed")
    assert doc["status"] == "running", "not closed until every target reports"
    doc = _tally(es, "run-mixed", "failed")

    assert doc["recovered_count"] == 1
    assert doc["failed_count"] == 2
    assert doc["status"] == "partial"
