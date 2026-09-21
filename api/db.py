import os
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
        }
    }
}

INDEX_BODIES = {
    SOURCES_INDEX: SOURCES_MAPPING,
    UPLOADS_INDEX: UPLOADS_MAPPING,
    BRONZE_INDEX: BRONZE_MAPPING,
    SILVER_INDEX: SILVER_MAPPING,
    DLQ_INDEX: DLQ_MAPPING,
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
    ]
    for name in names:
        if es.indices.exists(index=name):
            continue
        body = INDEX_BODIES.get(name, {})
        es.indices.create(index=name, body=body)
        print(f"Created OpenSearch index {name}", flush=True)


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