"""
Unit tests for the Module 2 API helper functions (routes + models).
No live services required — ES calls are faked or not reached.
"""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from db import BRONZE_INDEX, SILVER_INDEX, DLQ_INDEX

from routes.events import build_event_query
from routes.sources import SourceCreate, SourceUpdate, make_source_id, to_source_doc
from routes.sources import update_source as sources_update_source
from routes.uploads import sanitize_filename as upload_sanitize
from routes.uploads import upload_event_counts
import routes.sources as sources_mod
import routes.uploads as uploads_mod


def test_create_source_accepts_valid_file_source():
    source = SourceCreate(
        name="FW Logs",
        source_type="network_device",
        transport="file",
        expected_format="mixed",
    )
    assert source.transport == "file"
    assert source.enabled is True


@pytest.mark.parametrize(
    "transport", ["udp", "http", "file", "other"]
)
def test_allowed_transports(transport):
    source = SourceCreate(
        name="t", source_type="server", transport=transport
    )
    assert source.transport == transport


@pytest.mark.parametrize(
    "field,bad",
    [
        ("transport", "syslog"),
        ("source_type", "network-server"),
        ("expected_format", "yaml"),
    ],
)
def test_invalid_literals_rejected(field, bad):
    data = {"name": "t", "source_type": "server", "transport": "file"}
    data[field] = bad
    with pytest.raises(ValidationError):
        SourceCreate(**data)


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        SourceCreate(
            name="t",
            source_type="server",
            transport="file",
            sneaky="x",
        )


def test_make_source_id_uses_slug():
    sid = make_source_id("My Firewall Rules")
    assert sid.startswith("my-firewall-rules-")
    assert len(sid) == len("my-firewall-rules-") + 6


def test_make_source_id_falls_back_on_empty():
    assert make_source_id("!!!").startswith("source-")


def test_to_source_doc_defaults():
    doc = to_source_doc(
        "src-1",
        SourceCreate(name="x", source_type="server", transport="udp").model_dump(),
    )
    assert doc["source_id"] == "src-1"
    assert doc["enabled"] is True
    assert doc["expected_format"] == "mixed"
    assert doc["last_upload_at"] is None


def test_build_event_query_filters():
    body = build_event_query(
        filters={"source_id": "src-1", "severity": "low", "parser_id": "syslog"},
        limit=25,
    )
    assert body["size"] == 25
    must = body["query"]["bool"]["filter"]
    assert {"term": {"extensions.source_id.keyword": "src-1"}} in must
    assert {"term": {"severity.keyword": "low"}} in must
    assert {"term": {"parser_id.keyword": "syslog"}} in must


def test_build_event_query_adds_time_range():
    body = build_event_query(
        filters={"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
        limit=50,
    )
    must = body["query"]["bool"]["filter"]
    assert {"range": {"time": {"gte": "2026-01-01T00:00:00Z", "lte": "2026-01-02T00:00:00Z"}}} in must


def test_build_event_query_without_filters_has_no_query():
    body = build_event_query({}, limit=50)
    assert "query" not in body


def test_upload_filename_sanitized_by_api():
    assert upload_sanitize("../../../secret.log") == "secret.log"
    assert upload_sanitize("a/b/c.log") == "c.log"
    assert upload_sanitize("") == "upload.log"


class FakeES:
    """Minimal OpenSearch fake that supports the scroll used by
    upload_event_counts (search -> scroll -> clear_scroll)."""

    def __init__(self, raw_ids):
        self._raw_ids = raw_ids

    def search(self, index, body=None, **kwargs):
        return {
            "_scroll_id": "scroll-1",
            "_shards": {"successful": 1, "skipped": 0, "total": 1},
            "hits": {
                "total": {"value": len(self._raw_ids)},
                "hits": [{"_source": {"event_id": rid}} for rid in self._raw_ids],
            },
        }

    def scroll(self, body=None, **kwargs):
        return {"_scroll_id": "scroll-1", "hits": {"hits": []}}

    def clear_scroll(self, body=None, **kwargs):
        return {}

    def count(self, index, body):
        return {"count": len(self._raw_ids)}


def test_upload_event_counts_zero_when_no_bronze(monkeypatch):
    class NoHits(FakeES):
        def search(self, index, body=None, **kwargs):
            return {"_scroll_id": "s", "_shards": {"successful": 1, "total": 1}, "hits": {"hits": []}}

    monkeypatch.setattr(uploads_mod, "get_opensearch_client", lambda: NoHits([]))

    counts = upload_event_counts("u-nope")
    assert counts == {"raw_events": 0, "normalized_events": 0, "dlq_events": 0}


def test_upload_event_counts_derives_silver_and_dlq(monkeypatch):
    raw_ids = ["r-1", "r-2", "r-3"]

    class FakeCountES(FakeES):
        def __init__(self, raw_ids):
            super().__init__(raw_ids)
            self._counts = []

        def count(self, index, body):
            self._counts.append((index, body))
            # Silver normalizes 2 of 3; DLQ 1 of 3 (two chunked calls each).
            if index == SILVER_INDEX:
                return {"count": 2}
            if index == DLQ_INDEX:
                return {"count": 1}
            return {"count": len(self._raw_ids)}

    es = FakeCountES(raw_ids)
    monkeypatch.setattr(uploads_mod, "get_opensearch_client", lambda: es)

    counts = upload_event_counts("u-1")
    assert counts == {"raw_events": 3, "normalized_events": 2, "dlq_events": 1}

    # Exact Silver/DLQ queries must be the two 1000-chunks of raw ids.
    fetched = [b["query"]["terms"]["raw_event_id.keyword"] for _, b in es._counts]
    assert fetched == [raw_ids, raw_ids]


def test_upload_event_counts_uses_upload_id_filter(monkeypatch):
    captured = {}

    class CapturingES(FakeES):
        def search(self, index, body=None, **kwargs):
            captured["index"] = index
            captured["query"] = body["query"]
            return {"_scroll_id": "s", "_shards": {"successful": 1, "total": 1}, "hits": {"hits": []}}

    monkeypatch.setattr(uploads_mod, "get_opensearch_client", lambda: CapturingES([]))
    upload_event_counts("upload-42")

    assert captured["index"] == BRONZE_INDEX
    assert captured["query"] == {"term": {"upload_id": "upload-42"}}


class FauxUpdateES:
    def __init__(self):
        self.updates = []

    def update(self, index, id, body=None, **kwargs):
        self.updates.append((index, id, body))


SAMPLE_SOURCE = {
    "source_id": "s-1",
    "name": "Firewall Logs",
    "source_type": "network_device",
    "transport": "file",
    "expected_format": "mixed",
    "enabled": True,
    "created_at": "2026-09-21T00:00:00+00:00",
    "last_seen_at": None,
    "last_upload_at": None,
    "description": None,
}


def test_update_source_flips_enabled(monkeypatch):
    state = dict(SAMPLE_SOURCE)

    def get_source(source_id):
        return state if source_id == "s-1" else None

    class Fake(FauxUpdateES):
        def update(self, index, id, body=None, **kwargs):
            state.update(body["doc"])
            super().update(index, id, body=body, **kwargs)

    fake = Fake()
    monkeypatch.setattr(sources_mod, "get_source", get_source)
    monkeypatch.setattr(sources_mod, "get_opensearch_client", lambda: fake)

    result = sources_update_source("s-1", SourceUpdate(enabled=False))
    assert result["source"]["enabled"] is False
    assert fake.updates == [("ulpf-sources", "s-1", {"doc": {"enabled": False}})]


def test_update_source_404_when_not_registered(monkeypatch):
    monkeypatch.setattr(sources_mod, "get_source", lambda source_id: None)
    monkeypatch.setattr(sources_mod, "get_opensearch_client", lambda: FauxUpdateES())

    with pytest.raises(HTTPException) as exc:
        sources_update_source("ghost", SourceUpdate(name="Ghost"))
    assert exc.value.status_code == 404


def test_update_source_rejects_empty_body(monkeypatch):
    monkeypatch.setattr(sources_mod, "get_source", lambda source_id: SAMPLE_SOURCE)
    monkeypatch.setattr(sources_mod, "get_opensearch_client", lambda: FauxUpdateES())

    with pytest.raises(HTTPException) as exc:
        sources_update_source("s-1", SourceUpdate())
    assert exc.value.status_code == 400


def test_update_source_rejects_extra_fields():
    with pytest.raises(ValidationError):
        SourceUpdate(enabled=True, source_type="server")