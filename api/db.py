import os
from typing import Optional

from opensearchpy import OpenSearch

_OPENSEARCH_CLIENT = None


def get_opensearch_client() -> OpenSearch:
    global _OPENSEARCH_CLIENT
    if _OPENSEARCH_CLIENT is None:
        url = os.getenv("OPENSEARCH_URL", "http://opensearch:9200")
        _OPENSEARCH_CLIENT = OpenSearch(
            hosts=[url],
            timeout=10,
        )
    return _OPENSEARCH_CLIENT


SOURCES_INDEX = os.getenv("SOURCES_INDEX", "ulpf-sources")
UPLOADS_INDEX = os.getenv("UPLOADS_INDEX", "ulpf-uploads")
SILVER_INDEX = os.getenv("SILVER_INDEX", "ulpf-silver")
DLQ_INDEX = os.getenv("DLQ_INDEX", "ulpf-dlq")
BRONZE_INDEX = os.getenv("BRONZE_INDEX", "ulpf-bronze")
REPROCESS_RUNS_INDEX = os.getenv("REPROCESS_RUNS_INDEX", "ulpf-reprocess-runs")
PARSERS_INDEX = os.getenv("PARSERS_INDEX", "ulpf-parsers")

SOURCES_MAPPING = {
    "mappings": {
        "properties": {
            "source_id": {"type": "keyword"},
            "name": {"type": "keyword"},
            "source_type": {"type": "keyword"},
            "transport": {"type": "keyword"},
            "expected_format": {"type": "keyword"},
            "enabled": {"type": "boolean"},
            "created_at": {"type": "date"},
            "last_seen_at": {"type": "date"},
            "last_upload_at": {"type": "date"},
            "description": {"type": "text"},
        }
    }
}

UPLOADS_MAPPING = {
    "mappings": {
        "properties": {
            "upload_id": {"type": "keyword"},
            "source_id": {"type": "keyword"},
            "filename": {"type": "keyword"},
            "status": {"type": "keyword"},
            "created_at": {"type": "date"},
            "started_at": {"type": "date"},
            "completed_at": {"type": "date"},
            "total_lines": {"type": "long"},
            "published_events": {"type": "long"},
            "blank_lines": {"type": "long"},
            "line_errors": {"type": "long"},
            "normalized_events": {"type": "long"},
            "dlq_events": {"type": "long"},
            "failure_reason": {"type": "text"},
        }
    }
}

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

# Silver/DLQ stay dynamically mapped except for the fields the API sorts
# on (Silvers by `time`, DLQ by `first_seen_at`). Without these mappings
# a fresh, empty index has no mapping for the sort field and OpenSearch
# rejects the query (400) — this keeps a brand-new stack fully working.
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
            # Module 3 recovery audit. DLQ documents written before Module 3
            # have no such fields; declaring them here keeps the index mapping
            # stable and makes the new axes queryable/sortable.
            "resolved_at": {"type": "date"},
            "resolution_status": {"type": "keyword"},
            "last_reprocess_id": {"type": "keyword"},
            "reprocess_count": {"type": "long"},
        }
    }
}

REPROCESS_RUNS_MAPPING = {
    "mappings": {
        "properties": {
            "reprocess_id": {"type": "keyword"},
            "created_at": {"type": "date"},
            "started_at": {"type": "date"},
            "completed_at": {"type": "date"},
            "status": {"type": "keyword"},
            "requested_count": {"type": "long"},
            "published_count": {"type": "long"},
            "recovered_count": {"type": "long"},
            "failed_count": {"type": "long"},
        }
    }
}

# The parser registry. Priority and status are mapped because the UI orders
# the chain by them and filters on status; an unmapped field in a fresh index
# cannot be sorted or filtered on.
PARSERS_MAPPING = {
    "mappings": {
        "properties": {
            "parser_id": {"type": "keyword"},
            "display_name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "status": {"type": "keyword"},
            "priority": {"type": "long"},
            "version": {"type": "long"},
            "is_builtin": {"type": "boolean"},
            "source_formats": {"type": "keyword"},
            "created_at": {"type": "date"},
            "updated_at": {"type": "date"},
            "last_test": {"type": "object", "enabled": False},
        }
    }
}

INDEX_BODIES = {
    SOURCES_INDEX: SOURCES_MAPPING,
    UPLOADS_INDEX: UPLOADS_MAPPING,
    BRONZE_INDEX: BRONZE_MAPPING,
    SILVER_INDEX: SILVER_MAPPING,
    DLQ_INDEX: DLQ_MAPPING,
    REPROCESS_RUNS_INDEX: REPROCESS_RUNS_MAPPING,
    PARSERS_INDEX: PARSERS_MAPPING,
}


def ensure_indices(es: OpenSearch = None, indices: list = None):
    """
    Create configured indices if they do not exist.
    Safe to call on every startup. Never touches existing indices.
    """
    es = es or get_opensearch_client()
    names = indices or [
        SOURCES_INDEX,
        UPLOADS_INDEX,
        BRONZE_INDEX,
        SILVER_INDEX,
        DLQ_INDEX,
        REPROCESS_RUNS_INDEX,
        PARSERS_INDEX,
    ]
    for name in names:
        if es.indices.exists(index=name):
            continue
        body = INDEX_BODIES.get(name, {})
        es.indices.create(index=name, body=body)
        print(f"Created OpenSearch index {name}", flush=True)


_FIELD_CACHE: dict = {}


def clear_field_cache() -> None:
    """
    Forget resolved field names.

    Silver and DLQ are dynamically mapped: a field only exists once a
    document has used it, and `_source_id` can be a real `keyword` in one
    index and text-with-a-keyword-subfield in another. A cached resolution
    therefore goes stale the first time a mapping changes, which is exactly
    what a run that wipes the indices does.
    """
    _FIELD_CACHE.clear()


def resolve_field(index: str, path: str) -> Optional[str]:
    """
    Resolve a dotted field path to the name OpenSearch can actually query.

    Returns the path unchanged when it is already a keyword/numeric/date
    field, `<path>.keyword` when it is text with a keyword subfield, and
    None when the field is not in the mapping at all.

    Hardcoding ".keyword" is right for one index and silently wrong for the
    other, and a wrong field name returns an empty result set -- which reads
    as "no records arrived" and sends you hunting a pipeline bug that does
    not exist. Asking the live mapping is the only way to tell the two apart.
    """
    key = (index, path)
    if key in _FIELD_CACHE:
        return _FIELD_CACHE[key]

    resolved: Optional[str] = None
    es = get_opensearch_client()
    try:
        mapping = es.indices.get_mapping(index=index)
        properties = mapping[index]["mappings"].get("properties", {})
        node = None
        current = properties
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                node = None
                break
            node = current[part]
            current = node.get("properties") if isinstance(node, dict) else None
        if isinstance(node, dict):
            if node.get("type") == "text" and "keyword" in (node.get("fields") or {}):
                resolved = f"{path}.keyword"
            else:
                resolved = path
    except Exception:
        resolved = None

    _FIELD_CACHE[key] = resolved
    return resolved


def get_source(source_id: str):
    """
    Fetch a source by id from ulpf-sources. Returns the document
    dict or None.
    """
    es = get_opensearch_client()
    if not es.exists(index=SOURCES_INDEX, id=source_id):
        return None
    doc = es.get(index=SOURCES_INDEX, id=source_id)
    return doc["_source"]


def get_bronze(event_id: str):
    """
    Fetch one raw event from Bronze by its event_id (the document _id).
    Returns the document dict or None.
    """
    es = get_opensearch_client()
    if not es.exists(index=BRONZE_INDEX, id=event_id):
        return None
    doc = es.get(index=BRONZE_INDEX, id=event_id)
    return doc["_source"]


# DLQ documents written before Module 3 predate these fields. Fill them in on
# read so every consumer (API response, UI) sees one consistent shape instead
# of branching on whether the key happens to be present.
DLQ_AUDIT_DEFAULTS = {
    "resolution_status": "unresolved",
    "resolved_at": None,
    "last_reprocess_id": None,
    "replay_reason": None,
    "attempt_history": [],
    "reprocess_count": 0,
}


def with_audit_defaults(record: dict) -> dict:
    """
    Return a DLQ record with the Module 3 recovery fields defaulted.

    Missing keys mean "written before Module 3", which is exactly an
    unresolved record that has never been replayed.
    """
    merged = dict(record or {})
    for key, default in DLQ_AUDIT_DEFAULTS.items():
        merged.setdefault(key, default)
    return merged


def get_dlq(dlq_id: str):
    """
    Fetch one DLQ record by id. Returns the document dict or None.
    """
    es = get_opensearch_client()
    if not es.exists(index=DLQ_INDEX, id=dlq_id):
        return None
    doc = es.get(index=DLQ_INDEX, id=dlq_id)
    return with_audit_defaults(doc["_source"])


def get_silver(event_id: str):
    """
    Fetch one Silver document by its _id (which is `<raw_event_id>-norm`).
    Returns the document dict or None.
    """
    es = get_opensearch_client()
    if not es.exists(index=SILVER_INDEX, id=event_id):
        return None
    doc = es.get(index=SILVER_INDEX, id=event_id)
    return doc["_source"]


def silver_exists_for_raw(raw_event_id: str) -> bool:
    """
    True when a normalized event exists in Silver for this raw event.

    Parsers derive the normalized event_id as `<raw_event_id>-norm`, so the
    normalized document has a deterministic id and a replay overwrites the
    same row instead of appending a duplicate.
    """
    es = get_opensearch_client()
    return bool(es.exists(index=SILVER_INDEX, id=f"{raw_event_id}-norm"))