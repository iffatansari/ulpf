"""
Contract tests for the reprocess API surface.

These exist because the three run-returning endpoints had drifted: single-POST
returned `published_count` while batch-POST and GET returned `published`. A
client polling a run had to know which endpoint it had called. The response is
now defined once in `serialize_run` and every endpoint funnels through it.
"""
import os
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1] / "api"
sys.path.insert(0, str(API_DIR))

os.environ.setdefault("OPENSEARCH_URL", "http://localhost:9200")

from routes.reprocess import serialize_run  # noqa: E402

RUN_FIELDS = {
    "reprocess_id",
    "status",
    "requested_count",
    "published_count",
    "recovered_count",
    "failed_count",
    "reason",
    "dlq_ids",
    "event_ids",
    "errors",
    "created_at",
    "started_at",
    "completed_at",
}

SAMPLE = {
    "reprocess_id": "run-1",
    "status": "completed",
    "requested_count": 2,
    "published_count": 2,
    "recovered_count": 1,
    "failed_count": 1,
    "reason": "parser fix",
    "dlq_ids": ["a", "b"],
    "event_ids": ["r1", "r2"],
    "errors": [],
    "created_at": "2026-09-27T10:00:00+00:00",
    "started_at": "2026-09-27T10:00:01+00:00",
    "completed_at": "2026-09-27T10:00:09+00:00",
}


def test_serialize_run_exposes_the_stored_field_names():
    body = serialize_run(dict(SAMPLE))

    assert set(body) == RUN_FIELDS
    assert body["requested_count"] == 2
    assert body["published_count"] == 2
    assert body["recovered_count"] == 1
    assert body["failed_count"] == 1


def test_serialize_run_never_emits_the_old_short_aliases():
    """The renamed fields must not linger as aliases alongside the real ones."""
    body = serialize_run(dict(SAMPLE))

    for alias in ("requested", "published", "recovered", "failed"):
        assert alias not in body


def test_serialize_run_defaults_counters_when_absent():
    body = serialize_run({"reprocess_id": "run-2", "status": "running"})

    assert body["requested_count"] == 0
    assert body["published_count"] == 0
    assert body["recovered_count"] == 0
    assert body["failed_count"] == 0
    assert body["dlq_ids"] == []
    assert body["errors"] == []


def test_run_model_and_serializer_agree_on_field_names():
    """
    The serializer must not invent names the schema does not have, otherwise
    OpenSearch and the API would disagree about the same run.
    """
    sys.path.insert(0, str(API_DIR.parent))
    from schema.reprocess_run import ReprocessRun

    assert RUN_FIELDS == set(ReprocessRun.model_fields)


@pytest.mark.parametrize("reason", [None, "", "x" * 500, "x" * 501])
def test_reason_length_is_validated_at_the_edge(reason):
    from routes.dlq import BatchReprocessRequest, ReprocessRequest

    if reason is not None and len(reason) > 500:
        with pytest.raises(Exception):
            BatchReprocessRequest(dlq_ids=["a"], reason=reason)
    else:
        assert BatchReprocessRequest(dlq_ids=["a"], reason=reason).reason == reason
        assert ReprocessRequest(reason=reason).reason == reason


def test_dry_run_is_accepted_in_the_body_of_both_reprocess_routes():
    """
    `dry_run` is read from the query string on the single route but from the
    body on the batch route. That asymmetry is a trap: an operator who puts
    the flag in the other place gets a REAL replay with no warning, which is
    the opposite of what a dry run is for. Both routes now accept both, so
    this locks the models and the wiring together.
    """
    from routes.dlq import BatchReprocessRequest, ReprocessRequest

    assert ReprocessRequest(dry_run=True).dry_run is True
    assert BatchReprocessRequest(dlq_ids=["a"], dry_run=True).dry_run is True
    # Default stays off, so an absent flag never silently previews.
    assert ReprocessRequest().dry_run is False
    assert BatchReprocessRequest(dlq_ids=["a"]).dry_run is False
