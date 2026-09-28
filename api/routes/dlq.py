import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from db import (
    get_bronze,
    get_opensearch_client,
    with_audit_defaults,
)
from replay_bus import ReplayPublisher
from orchestrator.dlq.reprocess import dry_run_record, replay_records
from routes.reprocess import serialize_run


router = APIRouter()


class ReprocessRequest(BaseModel):
    reason: Optional[str] = Field(
        None,
        description="Why the operator is replaying, stored on the DLQ record for audit",
        max_length=500,
    )
    dry_run: bool = Field(
        False,
        description="Evaluate the parser chain without publishing or writing anything",
    )


class BatchReprocessRequest(BaseModel):
    dlq_ids: List[str] = Field(
        ...,
        description="Explicit DLQ ids to replay. Never a query -- replays are always deliberate.",
        min_length=1,
    )
    reason: Optional[str] = Field(None, max_length=500)
    dry_run: bool = Field(
        False,
        description="Evaluate the parser chain without publishing or writing anything",
    )


@router.get("/dlq")
def list_dlq(limit: int = Query(50, le=500)):
    """
    List DLQ records, newest first.

    Records written before Module 3 have no recovery fields, so they are
    normalised on read (they are, by definition, unresolved and never
    replayed) rather than being served with missing keys for the UI to
    special-case.
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
        "records": [with_audit_defaults(h["_source"]) for h in hits],
    }


@router.get("/dlq/{dlq_id}")
def get_dlq_record(dlq_id: str):
    """
    Return one DLQ record with its Bronze raw event for lineage.

    The Bronze document is the same document a replay would republish, so
    this is what the operator inspects before deciding to reprocess.
    """
    es = get_opensearch_client()
    index = os.getenv("DLQ_INDEX", "ulpf-dlq")

    if not es.exists(index=index, id=dlq_id):
        raise HTTPException(status_code=404, detail=f"dlq record not found: {dlq_id}")

    record = with_audit_defaults(es.get(index=index, id=dlq_id)["_source"])

    raw_event_id = record.get("raw_event_id")
    raw = get_bronze(raw_event_id) if raw_event_id else None

    return {
        "dlq": record,
        "raw": raw,
    }


def _publisher(request: Request) -> ReplayPublisher:
    publisher = getattr(request.app.state, "replay_publisher", None)
    if publisher is None:
        raise HTTPException(
            status_code=503,
            detail="replay publisher is not available",
        )
    return publisher


@router.post("/dlq/{dlq_id}/reprocess")
async def reprocess_one(
    dlq_id: str,
    request: Request,
    body: Optional[ReprocessRequest] = None,
    dry_run: bool = Query(
        False, description="Report the expected result without replaying"
    ),
):
    """
    Replay one DLQ event through the real pipeline.

    Resolves the record, reads the authoritative Bronze event and
    republishes it to `logs.raw` with replay headers. It returns as soon as
    the event is queued -- the caller polls /reprocess/{id} for the outcome,
    so a slow or large replay never holds the request open.

    `dry_run=true` evaluates the parser chain and writes nothing at all.

    `dry_run` is accepted in the query string or in the body. Both are honoured
    on purpose: a preview must never be able to turn into a real replay just
    because the operator put the flag in the "wrong" place.
    """
    es = get_opensearch_client()
    index = os.getenv("DLQ_INDEX", "ulpf-dlq")

    if not es.exists(index=index, id=dlq_id):
        raise HTTPException(status_code=404, detail=f"dlq record not found: {dlq_id}")

    if dry_run or (body.dry_run if body else False):
        try:
            return dry_run_record(es=es, dlq_id=dlq_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"dry run failed: {type(exc).__name__}: {exc}"
            ) from exc

    reason = body.reason if body else None

    run = await replay_records(
        producer=_publisher(request).producer,
        es=es,
        dlq_ids=[dlq_id],
        reason=reason,
    )

    # Same shape as POST /dlq/reprocess and GET /reprocess/{id}: one
    # representation of a run regardless of which endpoint produced it.
    return {**serialize_run(run.model_dump(mode="json")), "dlq_id": dlq_id}


@router.post("/dlq/reprocess")
async def reprocess_batch(
    request: Request,
    body: BatchReprocessRequest,
    dry_run: bool = Query(
        False, description="Report the expected result without replaying"
    ),
):
    """
    Replay several DLQ events as one auditable run.

    Ids are always explicit. A "reprocess everything matching a filter"
    mode sounds convenient but makes it far too easy to replay thousands of
    records by accident with no way to say which ones were meant.

    As on the single-record route, `dry_run` is honoured from the query
    string or the body, so a preview cannot silently become a real replay.
    """
    es = get_opensearch_client()

    if body.dry_run or dry_run:
        previews = []
        for dlq_id in body.dlq_ids:
            try:
                previews.append(dry_run_record(es=es, dlq_id=dlq_id))
            except LookupError as exc:
                previews.append(
                    {"dlq_id": dlq_id, "dry_run": True, "error": str(exc)}
                )
            except Exception as exc:
                previews.append(
                    {
                        "dlq_id": dlq_id,
                        "dry_run": True,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return {
            "dry_run": True,
            "requested": len(body.dlq_ids),
            "would_succeed": sum(1 for p in previews if p.get("would_succeed")),
            # Counted separately so "would_succeed: 0" is never read as
            # "nothing here is recoverable" when some verdicts rest on the
            # orchestrator's live Drain3 miner rather than on this process.
            "drain3_dependent": sum(1 for p in previews if p.get("drain3_dependent")),
            "results": previews,
        }

    run = await replay_records(
        producer=_publisher(request).producer,
        es=es,
        dlq_ids=body.dlq_ids,
        reason=body.reason,
    )

    return serialize_run(run.model_dump(mode="json"))
