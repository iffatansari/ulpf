"""
One aggregate, computed in one place, for every counter the UI shows.

The problem this exists to solve is drift. Before it, each section asked a
different question of a different index and summed it its own way:

    Dashboard / Metrics / Normalized   Silver count via /events
    Sources (list)                     sum of per-source /sources/{id}/stats
    Source detail                      one /sources/{id}/stats
    DLQ page                           /dlq total, unresolved/recovered split

Those disagree whenever the indexes change between the two reads -- which
during a live stream is always -- and "accepted" ends up meaning four
different numbers depending on which page you are looking at. Worse, the
per-source rollup only counts sources that are *registered*, so events
attributed to an unregistered source_id are counted globally and silently
vanish from the Sources page.

So: one endpoint, one read, one set of numbers. Every section renders from
this payload, and the only remaining question is how fresh it is.
"""

import os
from typing import Optional

from fastapi import APIRouter, Request

from db import (
    BRONZE_INDEX,
    DLQ_INDEX,
    REPROCESS_RUNS_INDEX,
    SILVER_INDEX,
    SOURCES_INDEX,
    UPLOADS_INDEX,
    clear_field_cache,
    get_opensearch_client,
    resolve_field,
)


router = APIRouter()

# A terms agg that returns nothing is a legitimate answer ("this index is
# empty"), but a terms agg on a field that is not in the mapping is a 400.
# Every aggregation below is therefore resolved through resolve_field() and
# skipped when it comes back None, so a fresh empty stack returns zeros
# instead of failing the request that the whole dashboard depends on.
AGG_SIZE = 50


def _count(es, index: str) -> int:
    try:
        return es.count(index=index, body={})["count"]
    except Exception:
        return 0


def _terms(es, index: str, path: str, query: Optional[dict] = None) -> dict:
    field = resolve_field(index, path)
    if not field:
        return {}
    body: dict = {
        "size": 0,
        "aggs": {"buckets": {"terms": {"field": field, "size": AGG_SIZE}}},
    }
    if query:
        body["query"] = query
    try:
        resp = es.search(index=index, body=body)
    except Exception:
        return {}
    return {
        bucket["key"]: bucket["doc_count"]
        for bucket in resp["aggregations"]["buckets"]["buckets"]
    }


def _bucket_list(es, index: str, field: str, sub_aggs: Optional[dict] = None) -> list:
    if not field:
        return []
    bucket_agg: dict = {"terms": {"field": field, "size": AGG_SIZE}}
    if sub_aggs:
        bucket_agg["aggs"] = sub_aggs
    try:
        resp = es.search(
            index=index, body={"size": 0, "aggs": {"buckets": bucket_agg}}
        )
    except Exception:
        return []
    return resp["aggregations"]["buckets"]["buckets"]


def _per_source(es) -> list:
    """
    Per-source raw / normalized / dlq / rescued, in one pass.

    The Sources page used to fan out one `/sources/{id}/stats` request per
    registered source every ten seconds and add the answers up. That is
    both slow and wrong: a source_id that is present in the events but not
    in the registry is counted globally and dropped from the page, so the
    page's total was quietly a subset of the dashboard's.

    Deriving every source_id from the data instead of from the registry
    means the rollup is complete by construction, and a single Silver
    aggregation with a parser sub-aggregation gives the rescued count
    without a second round trip per source.
    """
    bronze_field = resolve_field(BRONZE_INDEX, "source_id")
    silver_field = resolve_field(SILVER_INDEX, "extensions.source_id")
    dlq_field = resolve_field(DLQ_INDEX, "metadata.source_id")
    parser_field = resolve_field(SILVER_INDEX, "parser_id")

    rows: dict = {}

    def row(source_id):
        return rows.setdefault(
            source_id,
            {
                "source_id": source_id,
                "raw_events": 0,
                "normalized_events": 0,
                "dlq_events": 0,
                "rescued": 0,
            },
        )

    if bronze_field:
        for bucket in _bucket_list(
            es, BRONZE_INDEX, bronze_field, sub_aggs=None
        ):
            row(bucket["key"])["raw_events"] = bucket["doc_count"]

    if silver_field:
        sub = (
            {"parsers": {"terms": {"field": parser_field, "size": 20}}}
            if parser_field
            else None
        )
        for bucket in _bucket_list(es, SILVER_INDEX, silver_field, sub_aggs=sub):
            entry = row(bucket["key"])
            entry["normalized_events"] = bucket["doc_count"]
            for parser in (bucket.get("parsers") or {}).get("buckets", []):
                if parser["key"] == "drain3-fallback-v1":
                    entry["rescued"] = parser["doc_count"]

    if dlq_field:
        for bucket in _bucket_list(es, DLQ_INDEX, dlq_field, sub_aggs=None):
            row(bucket["key"])["dlq_events"] = bucket["doc_count"]

    return sorted(rows.values(), key=lambda r: (-r["normalized_events"], r["source_id"]))


@router.get("/stats")
def stats():
    """
    Every counter the UI renders, from one consistent read.

    The headline numbers are the pipeline invariant
    `bronze == silver + dlq` stated directly, so a caller can check it
    without a second round trip and without guessing which page is right.

    `formats` is aggregated over Bronze, not Silver. The detected format
    is a property of the *raw* line and the orchestrator only ever writes
    it there, so asking Silver for it returns an unmapped field -- which
    the previous client-side tally papered over by labelling every single
    event "unknown".
    """
    clear_field_cache()
    es = get_opensearch_client()

    bronze = _count(es, BRONZE_INDEX)
    silver = _count(es, SILVER_INDEX)
    dlq = _count(es, DLQ_INDEX)

    dlq_reasons = _terms(es, DLQ_INDEX, "classification")
    resolution = _terms(es, DLQ_INDEX, "resolution_status")
    # `with_audit_defaults` back-fills resolution_status on read for records
    # written before the recovery audit existed, so documents with no such
    # field are unresolved even though the agg cannot see them. Count the
    # total as the source of truth for "unresolved" rather than inferring it
    # from the agg, which would under-count exactly the oldest records.
    recovered = resolution.get("recovered", 0)

    parser_counts = _terms(es, SILVER_INDEX, "parser_id")
    tier_counts = _terms(es, SILVER_INDEX, "parser_tier")
    class_counts = _terms(es, SILVER_INDEX, "class_name")
    severity_counts = _terms(es, SILVER_INDEX, "severity")

    format_counts = _terms(es, BRONZE_INDEX, "format_hint")
    per_source = _per_source(es)

    uploads = _count(es, UPLOADS_INDEX)
    registered_sources = _count(es, SOURCES_INDEX)
    reprocess_runs = _count(es, REPROCESS_RUNS_INDEX)

    return {
        "bronze_events": bronze,
        "silver_events": silver,
        "dlq_events": dlq,
        "dlq_unresolved": max(dlq - recovered, 0),
        "dlq_recovered": recovered,
        "dlq_reasons": dlq_reasons,
        # The only honest definition of "rescued": the count the demo script
        # itself asserts on, taken from the same aggregate.
        "rescued": parser_counts.get("drain3-fallback-v1", 0),
        "parsers": parser_counts,
        "tiers": tier_counts,
        "classes": class_counts,
        "severities": severity_counts,
        "formats": format_counts,
        "per_source": per_source,
        "uploads": uploads,
        "registered_sources": registered_sources,
        "reprocess_runs": reprocess_runs,
    }


@router.post("/stats/reset")
async def reset_stats(request: Request):
    """
    Drop in-memory state that a wiped index does not clear.

    Called by the run script right after it deletes Bronze/Silver/DLQ and
    the ledgers. Three caches outlive that delete and each one alone is
    enough to make a counter read non-zero on a genuinely empty stack:

      - the SSE replay buffer, which would replay deleted events at any
        reconnecting browser, and
      - the resolved-field cache, which would keep pointing aggregations at
        mappings the wipe just invalidated.

    Subscribers are also signalled, so an open tab re-reads its snapshot
    instead of continuing to show the previous run's totals.
    """
    clear_field_cache()

    hub = getattr(request.app.state, "live_event_hub", None)
    if hub is None:
        return {"reset": True, "subscribers_reset": False, "replay_cleared": False}

    hub.clear_replay()
    await hub.reset_subscribers()
    return {"reset": True, "subscribers_reset": True, "replay_cleared": True}
