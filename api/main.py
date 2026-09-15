import os
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from db import get_opensearch_client

app = FastAPI(title="ULPF API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For MVP; restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"service": "ulpf-api", "status": "ok"}


@app.get("/events")
def list_events(limit: int = Query(50, le=500)):
    """
    List normalized events from Silver index.
    """
    es = get_opensearch_client()
    index = os.getenv("SILVER_INDEX", "ulpf-silver")
    resp = es.search(
        index=index,
        body={
            "size": limit,
            "sort": [{"time": "desc"}],
        },
    )
    hits = resp["hits"]["hits"]
    return {
        "total": resp["hits"]["total"]["value"],
        "events": [h["_source"] for h in hits],
    }


@app.get("/dlq")
def list_dlq(limit: int = Query(50, le=500)):
    """
    List DLQ records.
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


@app.get("/sources")
def list_sources():
    """
    List registered log sources (MVP: static or from a simple file/DB).
    For now, return a static list; you can extend later.
    """
    return {
        "sources": [
            {
                "source_id": "syslog-source-1",
                "source_type": "server",
                "transport": "syslog",
                "format_hint": "syslog",
            },
            {
                "source_id": "http-json-source-1",
                "source_type": "application",
                "transport": "http_json",
                "format_hint": "json",
            },
        ]
    }