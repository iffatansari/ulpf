import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile
from opensearchpy import helpers

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

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploads")
FILE_COLLECTOR_URL = os.getenv("FILE_COLLECTOR_URL", "http://file_collector:8082")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
CHUNK_SIZE = 1024 * 1024  # 1 MiB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_filename(filename: str) -> str:
    """
    Reduce a user-supplied filename to a safe base name.
    """
    name = str(filename).replace("\\", "/")
    name = name.rsplit("/", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "upload.log"


def patch_upload(es, upload_id: str, **fields):
    es.update(
        index=UPLOADS_INDEX,
        id=upload_id,
        body={"doc": fields},
        retry_on_conflict=5,
    )


def upload_event_counts(upload_id: str) -> dict:
    """
    Derive exact per-upload counts from Bronze/Silver/DLQ.

    Raw events carry upload_id (stored on the Bronze doc by the
    orchestrator from the Kafka header). The raw_event_id of every
    Silver/DLQ record points back to Bronze, so a scroll over the
    Bronze event_ids for this upload (exact regardless of upload size)
    plus a chunked terms-count on Silver/DLQ gives exact counts.
    """
    es = get_opensearch_client()

    raw_ids = [
        hit["_source"]["event_id"]
        for hit in helpers.scan(
            es,
            index=BRONZE_INDEX,
            query={"query": {"term": {"upload_id": upload_id}}},
            _source=["event_id"],
            size=1000,
            preserve_order=False,
        )
    ]

    if not raw_ids:
        return {
            "raw_events": 0,
            "normalized_events": 0,
            "dlq_events": 0,
        }

    normalized = 0
    dlq = 0

    # Chunk the terms filters so very large uploads never exceed
    # OpenSearch's index.max_terms_count / boolean clause limits.
    BATCH = 1000
    for i in range(0, len(raw_ids), BATCH):
        chunk = raw_ids[i : i + BATCH]
        normalized += es.count(
            index=SILVER_INDEX,
            body={"query": {"terms": {"raw_event_id.keyword": chunk}}},
        )["count"]
        dlq += es.count(
            index=DLQ_INDEX,
            body={"query": {"terms": {"raw_event_id.keyword": chunk}}},
        )["count"]

    return {
        "raw_events": len(raw_ids),
        "normalized_events": normalized,
        "dlq_events": dlq,
    }


def enrich_upload_counts(docs: list) -> list:
    """
    Merge exact Bronze/Silver/DLQ counts into upload job documents for
    list-style endpoints (dashboard, per-source uploads), so every row
    shows converged totals rather than whatever the collector last
    stored. In-progress jobs keep their collector-owned counters.
    """
    out = []
    for doc in docs:
        if doc.get("status") in ("completed", "failed"):
            out.append({**doc, **upload_event_counts(doc["upload_id"])})
        else:
            out.append(doc)
    return out


@router.post("/sources/{source_id}/upload", status_code=202)
async def upload_source_file(
    source_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Accept one log file for a registered file-based source.

    Validates the source, streams the upload to the shared volume
    (never into RAM), records the job in ulpf-uploads, then notifies
    the File Collector in the background so the browser request
    returns immediately.
    """
    es = get_opensearch_client()

    source = get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_id}")
    if not source.get("enabled", True):
        raise HTTPException(status_code=400, detail="source is disabled")
    if source.get("transport") != "file":
        raise HTTPException(status_code=400, detail="source transport is not file")

    upload_id = str(uuid.uuid4())
    safe_name = sanitize_filename(file.filename or "upload.log")
    rel_path = f"{upload_id}_{safe_name}"
    dest_path = Path(UPLOAD_DIR) / rel_path

    Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

    record = {
        "upload_id": upload_id,
        "source_id": source_id,
        "filename": safe_name,
        "status": "queued",
        "created_at": _now(),
        "started_at": None,
        "completed_at": None,
        "total_lines": 0,
        "published_events": 0,
        "blank_lines": 0,
        "line_errors": 0,
        "normalized_events": 0,
        "dlq_events": 0,
    }
    es.index(index=UPLOADS_INDEX, id=upload_id, body=record)

    # ---------------------------------------------------------
    # Streaming save. Chunked reads keep memory flat for large files.
    # ---------------------------------------------------------
    size = 0
    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1024 * 1024:
                    dest_path.unlink(missing_ok=True)
                    patch_upload(
                        es,
                        upload_id,
                        status="failed",
                        completed_at=_now(),
                        failure_reason=f"file_too_large (max {MAX_UPLOAD_MB} MB)",
                    )
                    raise HTTPException(
                        status_code=413,
                        detail=f"file exceeds MAX_UPLOAD_MB={MAX_UPLOAD_MB}",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        dest_path.unlink(missing_ok=True)
        patch_upload(
            es,
            upload_id,
            status="failed",
            completed_at=_now(),
            failure_reason=f"save_failed: {exc}",
        )
        raise HTTPException(status_code=500, detail=f"failed to store upload: {exc}")

    if size == 0:
        dest_path.unlink(missing_ok=True)
        patch_upload(
            es,
            upload_id,
            status="failed",
            completed_at=_now(),
            failure_reason="empty_file",
        )
        raise HTTPException(status_code=400, detail="uploaded file is empty")

    # ---------------------------------------------------------
    # Source side effects: record activity timestamps.
    # ---------------------------------------------------------
    es.update(
        index=SOURCES_INDEX,
        id=source_id,
        body={"doc": {"last_upload_at": _now(), "last_seen_at": _now()}},
        retry_on_conflict=5,
    )

    # ---------------------------------------------------------
    # Hand off to the File Collector. Runs in the background so this
    # uploads request returns as soon as the file is stored.
    # ---------------------------------------------------------
    background_tasks.add_task(
        notify_collector,
        upload_id=upload_id,
        rel_path=rel_path,
        source_id=source_id,
        source_type=source.get("source_type"),
        expected_format=source.get("expected_format"),
    )

    return {
        "upload_id": upload_id,
        "source_id": source_id,
        "filename": safe_name,
        "status": "queued",
    }


async def notify_collector(
    upload_id: str,
    rel_path: str,
    source_id: str,
    source_type: str,
    expected_format: str,
):
    """
    POST /process on the internal File Collector service.
    """
    es = get_opensearch_client()

    payload = {
        "upload_id": upload_id,
        "file_path": rel_path,
        "source_id": source_id,
        "source_type": source_type,
        "expected_format": expected_format,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{FILE_COLLECTOR_URL}/process",
                json=payload,
            )
        if resp.status_code not in (200, 202):
            raise RuntimeError(
                f"collector returned {resp.status_code}: {resp.text}"
            )
    except Exception as exc:
        patch_upload(
            es,
            upload_id,
            status="failed",
            completed_at=_now(),
            failure_reason=f"file_collector_unavailable: {exc}",
        )


@router.get("/sources/{source_id}/uploads")
def source_uploads(source_id: str, limit: int = Query(50, le=500)):
    """
    List upload jobs belonging to one source.
    """
    es = get_opensearch_client()

    if get_source(source_id) is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_id}")

    resp = es.search(
        index=UPLOADS_INDEX,
        size=limit,
        body={
            "sort": [{"created_at": "desc"}],
            "query": {"term": {"source_id": source_id}},
        },
    )
    hits = resp["hits"]["hits"]
    return {
        "source_id": source_id,
        "total": resp["hits"]["total"]["value"],
        "uploads": enrich_upload_counts([h["_source"] for h in hits]),
    }


@router.get("/uploads/{upload_id}")
def get_upload(upload_id: str):
    """
    Return one upload/job record with its processing status.

    The File Collector sets status to "completed" as soon as it has
    published every line, but the orchestrator still needs to persist
    each event in Bronze and normalize it (Silver/DLQ). So exact
    Bronze/Silver/DLQ counts are derived from OpenSearch on EVERY read
    (not only at completion) and written back to the upload document,
    letting the UI poll until the counts settle:

        raw_events == published_events
        raw_events == normalized_events + dlq_events
    """
    es = get_opensearch_client()

    if not es.exists(index=UPLOADS_INDEX, id=upload_id):
        raise HTTPException(status_code=404, detail=f"upload not found: {upload_id}")

    doc = es.get(index=UPLOADS_INDEX, id=upload_id)["_source"]

    counts = upload_event_counts(upload_id)
    doc = {**doc, **counts}

    # Persist the derived counters so the stored record converges too.
    if doc.get("status") == "completed":
        es.update(
            index=UPLOADS_INDEX,
            id=upload_id,
            body={"doc": counts},
            retry_on_conflict=5,
        )

    return {"upload": doc}