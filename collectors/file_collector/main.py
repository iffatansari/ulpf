"""
File Collector — streams uploaded log files line by line into logs.raw.

Module 2 responsibilities:
  1. Accept an internal POST /process request from the ULPF API.
  2. Validate that the requested file stays inside the controlled upload
     directory (no arbitrary host filesystem paths).
  3. Stream the file line by line. Never file.read() the whole buffer.
  4. Detect a per-line format hint (cef / syslog / json / unknown).
  5. Wrap every non-blank line in a RawEventEnvelope with transport="file".
  6. Publish each envelope to logs.raw for the orchestrator.
  7. Track upload job progress in the ulpf-uploads index.

This service does NOT parse or normalize events. The orchestrator remains
responsible for parser selection, Bronze persistence, normalization and DLQ.

Decoding boundary (intentional):
  File bytes are decoded as UTF-8 with errors="replace". An invalid byte
  becomes U+FFFD inside the raw payload, so a single bad byte never aborts
  the whole file. The Module 2 "lossless" guarantee applies after this
  decoding step — the raw payload is the exact decoded line string.
"""

import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from aiokafka import AIOKafkaProducer
from fastapi import BackgroundTasks, FastAPI, HTTPException
from opensearchpy import OpenSearch
from pydantic import BaseModel, Field

from schema.raw_event import RawEventEnvelope


# ----------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------

REDPANDA_BROKER = os.getenv("REDPANDA_BROKER", "redpanda:29092")
RAW_TOPIC = os.getenv("RAW_TOPIC", "logs.raw")
COLLECTOR_ID = os.getenv("COLLECTOR_ID", "file-collector-1")
FILE_PORT = int(os.getenv("FILE_PORT", "8082"))

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploads")

OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://opensearch:9200")
UPLOADS_INDEX = os.getenv("UPLOADS_INDEX", "ulpf-uploads")

# # Encoding boundary — see module docstring.
FILE_ENCODING = os.getenv("FILE_ENCODING", "utf-8")
FILE_ERRORS = os.getenv("FILE_ERRORS", "replace")


class ProcessRequest(BaseModel):
    upload_id: str
    file_path: str
    source_id: str
    source_type: str
    expected_format: Optional[str] = Field(None)

    class Config:
        extra = "forbid"


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

def detect_format_hint(line: str) -> str:
    """
    Return a format hint for one log line.

    This is ONLY a hint. The orchestrator makes the final parser
    decision and remains authoritative.
    """
    stripped = line.strip()

    if stripped.startswith("CEF:"):
        return "cef"

    # RFC-style Syslog normally begins with a PRI value such as <34>.
    if re.match(r"^<\d{1,3}>", stripped):
        return "syslog"

    # Only spend effort on JSON parsing when the line clearly looks
    # like a JSON document (starts with { or [).
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
            return "json"
        except json.JSONDecodeError:
            return "unknown"

    return "unknown"


def resolve_within_upload_dir(upload_dir: str, file_path: str) -> Path:
    """
    Resolve file_path and refuse anything outside upload_dir.

    Guards against ..//, absolute paths and other traversal tricks.
    """
    base = Path(upload_dir).resolve()
    candidate = Path(file_path)

    if not candidate.is_absolute():
        candidate = base / candidate

    candidate = candidate.resolve()

    if not candidate.is_relative_to(base):
        raise ValueError(
            f"file_path resolves outside the upload directory: {candidate}"
        )

    return candidate


def sanitize_filename(filename: str) -> str:
    """
    Reduce a user-provided filename to a safe base name.
    Strips directories and shells out anything dangerous.
    """
    name = Path(filename).name
    name = name.replace("..", "").replace("/", "_").replace("\\", "_")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "upload.log"


def patch_upload(es: OpenSearch, upload_id: str, **fields):
    """
    Merge patch a document in ulpf-uploads. Creates it on first use.

    NOTE: OpenSearch's update() does NOT take a doc_as_upsert kwarg; the
    upsert behaviour is expressed in the request body instead, so the
    FIle Collector never depends on a pre-existing upload record.
    """
    es.update(
        index=UPLOADS_INDEX,
        id=upload_id,
        body={"doc": fields, "upsert": fields},
        retry_on_conflict=5,
    )


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


def ensure_upload_index(es: OpenSearch):
    if es.indices.exists(index=UPLOADS_INDEX):
        return
    es.indices.create(index=UPLOADS_INDEX, body=UPLOADS_MAPPING)
    print(f"Created OpenSearch index {UPLOADS_INDEX}", flush=True)


# ----------------------------------------------------------------
# Core streaming upload processor
# ----------------------------------------------------------------

async def run_upload(
    producer: AIOKafkaProducer,
    es: OpenSearch,
    upload_id: str,
    file_path: str,
    source_id: str,
    source_type: str,
) -> dict:
    """
    Stream one file line by line into logs.raw.

    Returns a final result dict. Sets ulpf-uploads status to either
    "completed" or "failed" (never silently swallows a broken file or
    a broken broker connection).
    """

    try:
        resolved = resolve_within_upload_dir(UPLOAD_DIR, file_path)
    except ValueError as exc:
        patch_upload(
            es,
            upload_id,
            status="failed",
            completed_at=_now_iso(),
            failure_reason=f"invalid_file_path: {exc}",
        )
        return {"status": "failed", "failure_reason": str(exc)}

    patch_upload(es, upload_id, status="processing", started_at=_now_iso())

    counts = {
        "total_lines": 0,
        "published_events": 0,
        "blank_lines": 0,
        "line_errors": 0,
    }

    try:
        with open(
            resolved,
            "r",
            encoding=FILE_ENCODING,
            errors=FILE_ERRORS,
            newline=None,
        ) as fh:

            # Line-by-line streaming. Memory use stays flat regardless
            # of the file size. Blank lines are skipped and counted.
            for line in fh:
                counts["total_lines"] += 1

                stripped = line.strip()

                if not stripped:
                    counts["blank_lines"] += 1
                    continue

                try:
                    event = RawEventEnvelope(
                        event_id=str(uuid.uuid4()),
                        source_id=source_id,
                        source_type=source_type,
                        transport="file",
                        format_hint=detect_format_hint(stripped),
                        raw_payload=stripped,
                        collector_id=COLLECTOR_ID,
                        envelope_schema_version="1.0.0",
                    )

                    await producer.send_and_wait(
                        RAW_TOPIC,
                        json.dumps(
                            event.model_dump(mode="json")
                        ).encode("utf-8"),
                        headers=[
                            ("upload_id", upload_id.encode("utf-8")),
                        ],
                    )

                    counts["published_events"] += 1

                except Exception as exc:
                    # A single bad line (e.g. a publish hiccup) is
                    # isolated and counted, not allowed to stop the
                    # remaining lines.
                    counts["line_errors"] += 1
                    print(
                        f"Line error for upload {upload_id}: {exc}",
                        flush=True,
                    )
                    continue
    except OSError as exc:
        patch_upload(
            es,
            upload_id,
            status="failed",
            completed_at=_now_iso(),
            failure_reason=f"file_unreadable: {exc}",
            **counts,
        )
        return {"status": "failed", "failure_reason": str(exc)}

    nonblank = counts["total_lines"] - counts["blank_lines"]

    failure_reason = None

    if nonblank == 0:
        # A file with nothing to process (blank-only) cannot complete.
        final_status = "failed"
        failure_reason = "no non-blank lines to process"
    elif counts["published_events"] == nonblank:
        final_status = "completed"
    elif counts["published_events"] > 0:
        # Some lines made it; report the partial failure honestly.
        final_status = "completed"
        print(
            f"Upload {upload_id}: {counts['line_errors']} line error(s) "
            f"after {counts['published_events']} published",
            flush=True,
        )
    else:
        final_status = "failed"
        failure_reason = f"no lines published ({counts['line_errors']} line errors)"

    patch_upload(
        es,
        upload_id,
        status=final_status,
        completed_at=_now_iso() if final_status == "completed" else None,
        failure_reason=failure_reason,
        **counts,
    )

    return {"status": final_status, **counts}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

    producer = AIOKafkaProducer(
        bootstrap_servers=REDPANDA_BROKER
    )

    await producer.start()

    es = OpenSearch(
        hosts=[OPENSEARCH_URL],
        timeout=10,
    )

    ensure_upload_index(es)

    app.state.producer = producer
    app.state.es = es

    print(
        f"File Collector started (upload dir: {UPLOAD_DIR})",
        flush=True,
    )

    try:
        yield
    finally:
        await producer.stop()


app = FastAPI(
    title="ULPF File Collector",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"service": COLLECTOR_ID, "status": "ok"}


@app.post("/process", status_code=202)
async def process_upload(
    request: ProcessRequest,
    background_tasks: BackgroundTasks,
):
    """
    Queue an uploaded file for line-by-line processing.

    The file must already exist under the shared upload directory.
    The upload record in ulpf-uploads is updated by the collector as the
    job runs, so the UI can poll GET /uploads/{upload_id} on the API.
    """

    try:
        resolved = resolve_within_upload_dir(
            UPLOAD_DIR,
            request.file_path,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"invalid file_path: {exc}",
        )

    if not resolved.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"file does not exist: {request.file_path}",
        )

    background_tasks.add_task(
        run_upload,
        app.state.producer,
        app.state.es,
        request.upload_id,
        request.file_path,
        request.source_id,
        request.source_type,
    )

    return {"status": "accepted", "upload_id": request.upload_id}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=FILE_PORT,
    )