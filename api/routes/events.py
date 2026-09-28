import asyncio
from collections import deque
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from db import (
    SILVER_INDEX,
    get_bronze,
    get_opensearch_client,
    get_source,
)
from live_events import (
    SSE_HEARTBEAT_SECONDS,
    SSE_RESET,
    LiveEventHub,
    SSECapacityError,
    event_matches_source,
    format_sse_event,
)


router = APIRouter()
MAX_SEEN_EVENT_IDS = 2048


def build_event_query(filters: dict, limit: int) -> dict:
    """
    Build the OpenSearch query for the Silver events list.

    Source/severity/parser are keyword filters against the normalized
    event. Optional start/end bound the event 'time' range.
    """
    must = []

    if filters.get("source_id") is not None:
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
    limit: int = Query(50, ge=1, le=500),
    source_id: Optional[str] = Query(None, min_length=1, max_length=200),
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


@router.get("/events/stream")
async def stream_events(
    request: Request,
    source_id: Optional[str] = Query(None, min_length=1, max_length=200),
):
    hub: Optional[LiveEventHub] = getattr(request.app.state, "live_event_hub", None)
    service = getattr(request.app.state, "live_event_service", None)
    last_event_id = getattr(request, "headers", {}).get("last-event-id")
    if last_event_id is not None:
        last_event_id = last_event_id[:200]
    if hub is None or (service is not None and not service.is_ready):
        raise HTTPException(status_code=503, detail="live event service is not ready")

    try:
        queue = await hub.subscribe(source_id)
    except SSECapacityError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    try:
        es = get_opensearch_client()
        body = build_event_query(filters={"source_id": source_id}, limit=50)
        snapshot = await run_in_threadpool(es.search, index=SILVER_INDEX, body=body)
        initial_events = [hit["_source"] for hit in snapshot["hits"]["hits"]]
        seen_order = deque()
        seen_ids = set()
        for event in initial_events:
            if isinstance(event, dict) and event.get("event_id") is not None:
                event_id = str(event["event_id"])
                if event_id not in seen_ids:
                    seen_ids.add(event_id)
                    seen_order.append(event_id)
        while len(seen_order) > MAX_SEEN_EVENT_IDS:
            seen_ids.discard(seen_order.popleft())

        def remember_event(event_id: str) -> bool:
            if event_id in seen_ids:
                return False
            seen_ids.add(event_id)
            seen_order.append(event_id)
            if len(seen_order) > MAX_SEEN_EVENT_IDS:
                seen_ids.discard(seen_order.popleft())
            return True

        replay_events = [
            event
            for event in hub.replay_since(last_event_id, source_id)
            if isinstance(event, dict)
            and (
                event.get("event_id") is None
                or remember_event(str(event["event_id"]))
            )
        ]

        async def event_stream():
            try:
                yield ": connected\n\n"
                for event in reversed(initial_events):
                    yield format_sse_event(event)
                for event in replay_events:
                    yield format_sse_event(event)
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(
                            queue.get(), timeout=SSE_HEARTBEAT_SECONDS
                        )
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    if event is SSE_RESET:
                        yield "event: reset\ndata: {}\n\n"
                        break
                    if not isinstance(event, dict):
                        continue
                    event_id = event.get("event_id")
                    if event_id is not None:
                        event_id = str(event_id)
                        if not remember_event(event_id):
                            continue
                    if event_matches_source(event, source_id):
                        yield format_sse_event(event)
            finally:
                await hub.unsubscribe(queue)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except asyncio.CancelledError:
        await hub.unsubscribe(queue)
        raise
    except Exception:
        await hub.unsubscribe(queue)
        raise


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
    limit: int = Query(50, ge=1, le=500),
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