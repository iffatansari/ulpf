import os

from fastapi import APIRouter, HTTPException, Query

from db import (
    DLQ_INDEX,
    get_bronze,
    get_opensearch_client,
)


router = APIRouter()


@router.get("/dlq")
def list_dlq(limit: int = Query(50, le=500)):
    """
    List DLQ records (inspect-only in Module 2 — no replay yet).
    """
    es = get_opensearch_client()
    index = os.getenv("DLQ_INDEX", "ulpf-dlq")
    resp = es.search(
        index=index,
        body={
            "size": limit,
            "sort": [{"first_seen_at": "desc"}],
        },
    )
    hits = resp["hits"]["hits"]
    return {
        "total": resp["hits"]["total"]["value"],
        "records": [h["_source"] for h in hits],
    }


@router.get("/dlq/{dlq_id}")
def get_dlq_record(dlq_id: str):
    """
    Return one DLQ record with its Bronze raw event for lineage.
    """
    es = get_opensearch_client()
    index = os.getenv("DLQ_INDEX", "ulpf-dlq")

    if not es.exists(index=index, id=dlq_id):
        raise HTTPException(status_code=404, detail=f"dlq record not found: {dlq_id}")

    record = es.get(index=index, id=dlq_id)["_source"]

    raw_event_id = record.get("raw_event_id")
    raw = get_bronze(raw_event_id)

    return {
        "dlq": record,
        "raw": raw,
    }