import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from db import (
    DLQ_INDEX,
    SILVER_INDEX,
    SOURCES_INDEX,
    UPLOADS_INDEX,
    ensure_indices,
    get_opensearch_client,
)
from routes.sources import router as sources_router
from routes.uploads import enrich_upload_counts, router as uploads_router
from routes.events import router as events_router
from routes.dlq import router as dlq_router
from routes.drain import router as drain_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure Module 2 indices exist before the API starts serving.
    ensure_indices()
    yield


app = FastAPI(title="ULPF API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For MVP; restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sources_router)
app.include_router(uploads_router)
app.include_router(events_router)
app.include_router(dlq_router)
app.include_router(drain_router)


@app.get("/")
def root():
    return {"service": "ulpf-api", "status": "ok"}


@app.get("/dashboard")
def dashboard(limit: int = Query(5, le=20)):
    """
    High-level dashboard numbers for the ULPF UI.
    Every value is computed directly from OpenSearch.
    """
    es = get_opensearch_client()

    sources = es.count(index=SOURCES_INDEX, body={})["count"]
    normalized = es.count(index=SILVER_INDEX, body={})["count"]
    dlq = es.count(index=DLQ_INDEX, body={})["count"]
    uploads = es.count(index=UPLOADS_INDEX, body={})["count"]

    recent_events = es.search(
        index=SILVER_INDEX,
        body={"size": limit, "sort": [{"time": "desc"}]},
    )["hits"]["hits"]

    recent_uploads = es.search(
        index=UPLOADS_INDEX,
        body={"size": limit, "sort": [{"created_at": "desc"}]},
    )["hits"]["hits"]

    return {
        "sources": sources,
        "normalized_events": normalized,
        "dlq_events": dlq,
        "uploads": uploads,
        "recent_events": [h["_source"] for h in recent_events],
        "recent_uploads": enrich_upload_counts(
            [h["_source"] for h in recent_uploads]
        ),
    }