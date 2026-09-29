"""
Contract tests for GET /stats and the field resolution it depends on.

`/stats` is now the only count contract the UI reads, so the thing worth
pinning is not that it returns numbers -- it is that the numbers are
*comparable to each other*. Every bug this endpoint exists to prevent was
a count that was internally plausible and disagreed with its neighbour:

  - a field resolved with a hardcoded ".keyword" returning an empty
    aggregation, which reads as "no events" rather than as an error;
  - a breakdown summed from a 50-row browser window and labelled "total";
  - a per-source rollup keyed on the source registry, so events from an
    unregistered source_id counted globally and vanished from the page.

So the assertions below are mostly cross-checks -- headroom against headroom
-- rather than absolute values.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
for path in (str(ROOT), str(ROOT / "orchestrator"), str(API_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault("OPENSEARCH_URL", "http://localhost:9200")

import db  # noqa: E402
from routes import stats as stats_route  # noqa: E402


class FakeES:
    """
    Enough OpenSearch for the aggregate, and no more.

    Deliberately unforgiving: a terms aggregation against an index that has
    no buckets registered raises, so a field the resolver wrongly resolves
    to a real keyword surfaces here as a failed request rather than as a
    silently wrong count. That is the failure mode these tests exist for.
    """

    def __init__(self, counts=None, mappings=None, aggs=None):
        self.counts = counts or {}
        self.mappings = mappings or {}
        self.aggs = aggs or {}
        self.indices = self

    def count(self, index, body):
        return {"count": self.counts.get(index, 0)}

    def search(self, index, body):
        if "aggs" not in body:
            raise AssertionError("a search was issued without an aggregation")
        if index not in self.aggs:
            raise RuntimeError(
                f"terms agg against unmapped {index}: the resolver should have skipped it"
            )
        return {"aggregations": {"buckets": {"buckets": self.aggs[index]}}}

    # --- indices API, used only by the resolver -------------------------
    def get_mapping(self, index):
        if index not in self.mappings:
            raise RuntimeError(f"index_not_found_exception: {index}")
        return {index: {"mappings": {"properties": self.mappings[index]}}}

    def exists(self, index):
        return index in self.mappings


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    db.clear_field_cache()
    yield
    db.clear_field_cache()


@pytest.fixture
def install(monkeypatch):
    def _install(es):
        monkeypatch.setattr(db, "get_opensearch_client", lambda: es)
        monkeypatch.setattr(stats_route, "get_opensearch_client", lambda: es)
        return es

    return _install


TEXT_WITH_KEYWORD = {
    "type": "text",
    "fields": {"keyword": {"type": "keyword"}},
}


def test_resolve_field_picks_the_keyword_subfield_for_text(install):
    install(
        FakeES(
            mappings={
                "ulpf-silver": {
                    "extensions": {"properties": {"source_id": TEXT_WITH_KEYWORD}}
                }
            }
        )
    )

    assert (
        db.resolve_field("ulpf-silver", "extensions.source_id")
        == "extensions.source_id.keyword"
    )


def test_resolve_field_leaves_a_real_keyword_alone(install):
    """Bronze declares `source_id` as a keyword; adding .keyword breaks it."""
    install(FakeES(mappings={"ulpf-bronze": {"source_id": {"type": "keyword"}}}))

    assert db.resolve_field("ulpf-bronze", "source_id") == "source_id"


def test_resolve_field_returns_none_for_an_unmapped_field(install):
    install(FakeES(mappings={"ulpf-silver": {}}))

    # Silver never stores format_hint. Returning a name anyway is what made
    # the old client-side tally label every event "unknown".
    assert db.resolve_field("ulpf-silver", "format_hint") is None


def test_resolve_field_survives_a_missing_index(install):
    install(FakeES(mappings={}))

    # A query against a non-existent index must not take down the aggregate
    # every page depends on.
    assert db.resolve_field("ulpf-silver", "parser_id") is None


def test_stats_on_a_completely_empty_stack_reports_all_zeros(install):
    """A fresh stack must return zeros, not 400s from aggs on empty indices."""
    install(FakeES())

    body = stats_route.stats()

    assert body["bronze_events"] == 0
    assert body["silver_events"] == 0
    assert body["dlq_events"] == 0
    assert body["dlq_unresolved"] == 0
    assert body["rescued"] == 0
    assert body["classes"] == {}
    assert body["formats"] == {}
    assert body["per_source"] == []


def test_stats_aggregates_formats_from_bronze_never_silver(install):
    """
    The format breakdown is a Bronze question.

    Asking Silver produced an unmapped field, which is why the Metrics
    "lines by detected format" chart showed a single `unknown` bar no
    matter how much traffic had been normalized. If this ever starts
    reading Silver again the FakeES will raise rather than quietly return
    an empty breakdown.
    """
    install(
        FakeES(
            counts={"ulpf-bronze": 3, "ulpf-silver": 3},
            mappings={
                "ulpf-bronze": {
                    "source_id": {"type": "keyword"},
                    "format_hint": {"type": "keyword"},
                },
                "ulpf-silver": {"class_name": TEXT_WITH_KEYWORD},
            },
            aggs={
                "ulpf-bronze": [
                    {"key": "syslog", "doc_count": 2},
                    {"key": "json", "doc_count": 1},
                ],
                "ulpf-silver": [{"key": "network_activity", "doc_count": 3}],
            },
        )
    )

    body = stats_route.stats()

    assert body["formats"] == {"syslog": 2, "json": 1}
    assert sum(body["formats"].values()) == body["bronze_events"]
    assert sum(body["classes"].values()) == body["silver_events"]


def test_stats_never_sums_a_browser_page_into_a_total(install):
    """
    The old bug, stated as an invariant.

    A breakdown that does not add up to its own headline is what a tally
    over a 50-row window looks like once the corpus outgrows the window.
    """
    install(
        FakeES(
            counts={"ulpf-bronze": 500, "ulpf-silver": 480, "ulpf-dlq": 20},
            mappings={
                "ulpf-bronze": {"format_hint": {"type": "keyword"}},
                "ulpf-silver": {"class_name": TEXT_WITH_KEYWORD},
            },
            aggs={
                "ulpf-bronze": [{"key": "syslog", "doc_count": 500}],
                "ulpf-silver": [
                    {"key": "network_activity", "doc_count": 300},
                    {"key": "authentication_activity", "doc_count": 180},
                ],
            },
        )
    )

    body = stats_route.stats()

    assert body["silver_events"] == 480
    assert sum(body["classes"].values()) == 480
    assert body["bronze_events"] == 500
    assert sum(body["formats"].values()) == 500
    # The invariant the whole pipeline rests on, as the UI now states it.
    assert body["bronze_events"] == body["silver_events"] + body["dlq_events"]


def test_per_source_rescued_comes_from_the_parser_sub_aggregation(install):
    """
    Rescued is a cross of source and parser.

    Taking it from `source.lastRun` in the browser, as the Source detail
    page did, returned 0 for every source fed by the live pipeline, because
    lastRun only exists after a browser-side sample run.
    """
    install(
        FakeES(
            counts={"ulpf-silver": 5},
            mappings={
                "ulpf-silver": {
                    "extensions": {
                        "properties": {
                            "source_id": TEXT_WITH_KEYWORD,
                            "parser_id": TEXT_WITH_KEYWORD,
                        }
                    }
                }
            },
            aggs={
                "ulpf-silver": [
                    {
                        "key": "sse-source-1",
                        "doc_count": 5,
                        "parsers": {
                            "buckets": [
                                {"key": "syslog-parser-v1", "doc_count": 3},
                                {"key": "drain3-fallback-v1", "doc_count": 2},
                            ]
                        },
                    }
                ]
            },
        )
    )

    body = stats_route.stats()

    assert body["per_source"] == [
        {
            "source_id": "sse-source-1",
            "raw_events": 0,
            "normalized_events": 5,
            "dlq_events": 0,
            "rescued": 2,
        }
    ]


def test_per_source_is_derived_from_data_not_from_the_registry(install):
    """
    An unregistered source_id still has to appear.

    The Sources page used to total only the sources the registry knew
    about, so events tagged with an id nobody registered were counted on
    the Dashboard and silently missing from the page -- the two totals
    were both honest and disagreed.
    """
    install(
        FakeES(
            counts={"ulpf-silver": 3},
            mappings={
                "ulpf-silver": {
                    "extensions": {
                        "properties": {
                            "source_id": TEXT_WITH_KEYWORD,
                            "parser_id": TEXT_WITH_KEYWORD,
                        }
                    }
                }
            },
            aggs={
                "ulpf-silver": [
                    {"key": "ulpf-demo-101500-stream", "doc_count": 3},
                ]
            },
        )
    )

    body = stats_route.stats()

    assert [row["source_id"] for row in body["per_source"]] == [
        "ulpf-demo-101500-stream"
    ]
    assert sum(row["normalized_events"] for row in body["per_source"]) == 3
    assert body["silver_events"] == 3


def test_dlq_unresolved_counts_records_that_predate_the_recovery_audit(install):
    """
    Records written before `resolution_status` existed have no such field.

    Deriving "unresolved" from the aggregation under-counts exactly the
    oldest records, so the index total is the source of truth and the
    recovered count is the subtraction.
    """
    install(
        FakeES(
            counts={"ulpf-dlq": 5},
            mappings={"ulpf-dlq": {"resolution_status": {"type": "keyword"}}},
            # Only the recovered record contributes a bucket. The other
            # four are pre-audit documents with no resolution_status.
            aggs={"ulpf-dlq": [{"key": "recovered", "doc_count": 3}]},
        )
    )

    body = stats_route.stats()

    assert body["dlq_events"] == 5
    assert body["dlq_recovered"] == 3
    assert body["dlq_unresolved"] == 2


def test_reset_clears_the_field_cache_and_the_replay_buffer():
    """
    Wiping the documents is not enough for the board to read zero.

    The resolver cache and the SSE replay buffer both outlive an index
    delete, and each one alone is enough to make a counter read non-zero
    on a genuinely empty stack.
    """
    import asyncio

    from live_events import LiveEventHub

    hub = LiveEventHub(replay_size=10)
    asyncio.run(hub.publish({"event_id": "1", "value": 1}))
    assert len(hub._replay) == 1

    db.clear_field_cache()
    hub.clear_replay()

    assert len(hub._replay) == 0
    assert hub.replay_since("1") == []
