import re
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db import (
    BRONZE_INDEX,
    DLQ_INDEX,
    SILVER_INDEX,
    SOURCES_INDEX,
    UPLOADS_INDEX,
    get_opensearch_client,
    get_source,
)


router = APIRouter()


class SourceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    source_type: Literal[
        "network_device",
        "server",
        "application",
        "database",
        "cloud",
        "iot",
        "custom",
    ] = Field(...)
    transport: Literal["udp", "http", "file", "other"] = Field(...)
    expected_format: Literal["auto", "mixed", "syslog", "json", "cef"] = Field(
        default="mixed"
    )
    enabled: bool = Field(default=True)
    description: Optional[str] = Field(default=None)

    class Config:
        extra = "forbid"


class Source(SourceCreate):
    source_id: str
    created_at: datetime
    last_seen_at: Optional[datetime] = None
    last_upload_at: Optional[datetime] = None


def make_source_id(name: str) -> str:
    """
    Human-friendly source id derived from the source name plus a
    short random suffix to avoid collisions.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        slug = "source"
    return f"{slug}-{uuid.uuid4().hex[:6]}"


def to_source_doc(source_id: str, data: dict) -> dict:
    return {
        "source_id": source_id,
        "name": data["name"],
        "source_type": data["source_type"],
        "transport": data["transport"],
        "expected_format": data.get("expected_format", "mixed"),
        "enabled": data.get("enabled", True),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_seen_at": None,
        "last_upload_at": None,
        "description": data.get("description"),
    }


@router.get("/sources")
def list_sources():
    """
    List all registered log sources from the Source Registry.
    """
    es = get_opensearch_client()
    resp = es.search(
        index=SOURCES_INDEX,
        body={
            "size": 1000,
            "sort": [{"created_at": "desc"}],
        },
    )
    sources = [h["_source"] for h in resp["hits"]["hits"]]
    return {"sources": sources}


@router.post("/sources", status_code=201)
def create_source(source: SourceCreate):
    """
    Register one log source in the Source Registry (ulpf-sources).
    Transport is one of: udp, http, file, other.
    """
    source_id = make_source_id(source.name)
    document = to_source_doc(source_id, source.model_dump())

    es = get_opensearch_client()
    es.index(
        index=SOURCES_INDEX,
        id=source_id,
        body=document,
    )

    return {"source": document, "source_id": source_id}


@router.get("/sources/{source_id}")
def get_source_by_id(source_id: str):
    """
    Return one source or 404.
    """
    doc = get_source(source_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_id}")
    return {"source": doc}


class SourceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    expected_format: Optional[Literal["auto", "mixed", "syslog", "json", "cef"]] = (
        Field(default=None)
    )
    enabled: Optional[bool] = Field(default=None)
    description: Optional[str] = Field(default=None)

    class Config:
        extra = "forbid"


@router.put("/sources/{source_id}")
def update_source(source_id: str, update: SourceUpdate):
    """
    Update the mutable fields of one source in the Source Registry.

    Immutable/internal fields (source_type, transport, created_at,
    last_seen_at, last_upload_at) are never overwritten here; they are
    owned by the collector/API pipeline when a source produces data.
    """
    es = get_opensearch_client()

    if get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_id}")

    updates = update.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no updatable fields supplied")

    es.update(
        index=SOURCES_INDEX,
        id=source_id,
        body={"doc": updates},
        retry_on_conflict=5,
    )

    return {"source": get_source(source_id)}


@router.delete("/sources/{source_id}", status_code=200)
def delete_source(source_id: str):
    """
    Remove one source from the Source Registry.
    """
    es = get_opensearch_client()

    if get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_id}")

    es.delete(index=SOURCES_INDEX, id=source_id)
    return {"deleted": source_id}


@router.get("/sources/{source_id}/stats")
def source_stats(source_id: str):
    """
    Compute real statistics for one source directly from OpenSearch:

      raw events        → Bronze (source_id)
      normalized events → Silver (extensions.source_id)
      DLQ events        → DLQ (metadata.source_id)
      format distribution → Bronze format_hint
      parser distribution → Silver parser_id
      blank / errored lines → summed from ulpf-uploads jobs
    """
    es = get_opensearch_client()

    if get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_id}")

    raw = es.count(
        index=BRONZE_INDEX,
        body={"query": {"term": {"source_id": source_id}}},
    )["count"]

    normalized = es.count(
        index=SILVER_INDEX,
        body={"query": {"term": {"extensions.source_id.keyword": source_id}}},
    )["count"]

    dlq = es.count(
        index=DLQ_INDEX,
        body={"query": {"term": {"metadata.source_id.keyword": source_id}}},
    )["count"]

    formats_agg = es.search(
        index=BRONZE_INDEX,
        body={
            "size": 0,
            "query": {"term": {"source_id": source_id}},
            "aggs": {"formats": {"terms": {"field": "format_hint", "size": 20}}},
        },
    )
    formats = {
        b["key"] or "none": b["doc_count"] for b in formats_agg["aggregations"]["formats"]["buckets"]
    }

    parsers_agg = es.search(
        index=SILVER_INDEX,
        body={
            "size": 0,
            "query": {"term": {"extensions.source_id.keyword": source_id}},
            "aggs": {"parsers": {"terms": {"field": "parser_id.keyword", "size": 20}}},
        },
    )
    parsers = {
        b["key"] or "none": b["doc_count"] for b in parsers_agg["aggregations"]["parsers"]["buckets"]
    }

    last_agg = es.search(
        index=BRONZE_INDEX,
        body={
            "size": 0,
            "query": {"term": {"source_id": source_id}},
            "aggs": {"last": {"max": {"field": "ingested_at"}}},
        },
    )
    last_ingested = last_agg["aggregations"]["last"].get("value_as_string")

    uploads_agg = es.search(
        index=UPLOADS_INDEX,
        body={
            "size": 0,
            "query": {"term": {"source_id": source_id}},
            "aggs": {
                "blank_lines": {"sum": {"field": "blank_lines"}},
                "line_errors": {"sum": {"field": "line_errors"}},
                "published_events": {"sum": {"field": "published_events"}},
            },
        },
    )
    uploads_aggs = uploads_agg["aggregations"]
    blank_lines = int(uploads_aggs["blank_lines"]["value"] or 0)
    line_errors = int(uploads_aggs["line_errors"]["value"] or 0)
    published_events = int(uploads_aggs["published_events"]["value"] or 0)

    success_rate = round(normalized / raw * 100, 2) if raw else 0.0

    return {
        "source_id": source_id,
        "raw_events": raw,
        "normalized_events": normalized,
        "dlq_events": dlq,
        "published_events": published_events,
        "blank_lines": blank_lines,
        "line_errors": line_errors,
        "formats": formats,
        "parsers": parsers,
        "last_ingested_at": last_ingested,
        "success_rate": success_rate,
    }