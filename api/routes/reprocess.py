import os

from fastapi import APIRouter, HTTPException

from db import get_opensearch_client


router = APIRouter()


@router.get("/reprocess/{reprocess_id}")
def get_reprocess_run(reprocess_id: str):
    """
    Poll a reprocess run.

    Kept separate from the DLQ routes because a run outlives any single
    record: the UI needs one id to watch for a batch, and the counters here
    are the orchestrator's own verdict rather than anything the API infers.
    """
    es = get_opensearch_client()
    index = os.getenv("REPROCESS_RUNS_INDEX", "ulpf-reprocess-runs")

    if not es.exists(index=index, id=reprocess_id):
        raise HTTPException(
            status_code=404, detail=f"reprocess run not found: {reprocess_id}"
        )

    run = es.get(index=index, id=reprocess_id)["_source"]

    return serialize_run(run)


def serialize_run(run: dict) -> dict:
    """
    Present a reprocess run using the persisted ReprocessRun field names.

    The counters deliberately keep the `_count` suffix used in the stored
    document and the schema, so a run looks identical whether it is read back
    from OpenSearch or returned straight from a POST. Renaming them here
    would force every client to handle two shapes of the same object.
    """
    return {
        "reprocess_id": run.get("reprocess_id"),
        "status": run.get("status"),
        "requested_count": run.get("requested_count", 0),
        "published_count": run.get("published_count", 0),
        "recovered_count": run.get("recovered_count", 0),
        "failed_count": run.get("failed_count", 0),
        "reason": run.get("reason"),
        "dlq_ids": run.get("dlq_ids", []),
        "event_ids": run.get("event_ids", []),
        "errors": run.get("errors", []),
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
    }
