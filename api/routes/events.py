import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from db import (
    SILVER_INDEX,
    get_bronze,
    get_opensearch_client,
    get_source,
)


router = APIRouter()


def build_event_query(filters: dict, limit: int) -> dict:
    """
    Build the OpenSearch query for the Silver events list.

    Source/severity/parser are keyword filters against the normalized
    event. Optional start/end bound the event 'time' range.
    """
    must = []

    if filters.get("source_id"):
        must.append(
            {"term": {"extensions.source_id.keyword": filters["source_id"]}}
        )
    if filters.get("severity"):
        must.append({"term": {"severity.keyword": filters["severity"]}})
    if filters.get("parser_id"):
        must.append({"term": {"parser_id.keyword": filters["parser_id"]}})

    time_range = {}
    if filters.get("start"):
        time_range["gte"] = filters["start"]
    if filters.get("end"):
        time_range["lte"] = filters["end"]
    if time_range:
        must.append({"range": {"time": time_range}})

    body = {
        "size": limit,
        "sort": [{"time": "desc"}],
    }
    if must:
        body["query"] = {"bool": {"filter": must}}

    return body


@router.get("/events")
def list_events(
    limit: int = Query(50, le=500),
    source_id: Optional[str] = None,
    severity: Optional[str] = None,
    parser_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
):
    """
    List normalized (Silver) events.

    Optional filters: source_id, severity, parser_id, and a time range
    via start/end ISO timestamps.
    """
    es = get_opensearch_client()
    body = build_event_query(
        filters={
            "source_id": source_id,
            "severity": severity,
            "parser_id": parser_id,
            "start": start,
            "end": end,
        },
        limit=limit,
    )

    resp = es.search(index=SILVER_INDEX, body=body)
    hits = resp["hits"]["hits"]
    return {
        "total": resp["hits"]["total"]["value"],
        "events": [h["_source"] for h in hits],
    }


@router.get("/events/{event_id}")
def get_event(event_id: str):
    """
    Return one normalized event together with its Bronze raw event,
    making the full lineage visible:

        normalized event
          └→ raw_event_id
               └→ Bronze raw event (original payload)
    """
    es = get_opensearch_client()

    if not es.exists(index=SILVER_INDEX, id=event_id):
        raise HTTPException(status_code=404, detail=f"event not found: {event_id}")

    normalized = es.get(index=SILVER_INDEX, id=event_id)["_source"]

    raw_event_id = normalized.get("raw_event_id")
    raw = get_bronze(raw_event_id)

    return {
        "normalized": normalized,
        "raw": raw,
    }


@router.get("/sources/{source_id}/events")
def source_events(
    source_id: str,
    limit: int = Query(50, le=500),
):
    """
    List normalized events produced by one source.
    Server-side filtering — no client-side database scraping.
    """
    if get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_id}")

    es = get_opensearch_client()
    body = build_event_query(filters={"source_id": source_id}, limit=limit)

    resp = es.search(index=SILVER_INDEX, body=body)
    hits = resp["hits"]["hits"]
    return {
        "source_id": source_id,
        "total": resp["hits"]["total"]["value"],
        "events": [h["_source"] for h in hits],
    }