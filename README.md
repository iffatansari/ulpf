# ULPF
### Universal Log Pre-Processing Framework

A containerized framework for collecting logs from different sources, processing them through a common pipeline, normalizing events, and preparing them for further analysis.

**Pipeline:**  
`Log Source → Collector → Redpanda → Normalizer → OpenSearch`

---

## Quick Start

### Prerequisites
- Git
- Docker Desktop

### 1. Clone

```bash
git clone https://github.com/iffatansari/ulpf.git
cd ulpf
```

### 2. Start ULPF - Run

```bash
docker compose up --build -d
```

### 3. Check the services

```bash
docker compose ps
```
All services should be Up. Redpanda and OpenSearch should show Healthy.

### 4. Open the applications

- UI: http://localhost:3000
- API: http://localhost:8000
- HTTP Log Collector: http://localhost:8081

**ULPF is ready to use.**

---

## Module 2 — File Ingestion & Source Onboarding

Module 2 adds file-based log onboarding on top of the Module 1 pipeline. Every
raw event is persisted **losslessly in Bronze** *before* parsing, so the original
payload can always be traced back from any normalized event or DLQ record.

### Flow

```
Source Registry (api) ─┐
  POST /sources        │      uploads a log file
                       ▼
        ┌──────────────────────┐         ┌───────────────────┐
        │  ULPF API  :8000     │ ──────► │  File Collector    │
        └──────────────────────┘  POST    │  (internal :8082) │
                       ▲       /process   └────────┬──────────┘
                       │                           │ streams lines
    GET /uploads/{id}  │                           ▼
        (poll status)  │                    logs.raw (Redpanda)
                       └────── counts ◄──────────┤
                                                 ▼
                                OpenSearch: ulpf-bronze (raw, before parse)
                                            ulpf-silver (normalized)
                                            ulpf-dlq    (failed)
```

### Register a file source and upload

```bash
# 1. Register a source (transport must be "file")
curl -s -X POST http://localhost:8000/sources -H "Content-Type: application/json" \
  -d '{"name":"FW Logs","source_type":"network_device","transport":"file","expected_format":"mixed"}'

# 2. Upload a log file (returns upload_id; job runs in background)
curl -s -X POST http://localhost:8000/sources/<source_id>/upload \
  -F "file=@demo/mixed_security_logs.log"

# 3. Poll the upload job until completed
curl -s http://localhost:8000/uploads/<upload_id>
```

### What Module 2 delivers
- **Bronze persistence** — every raw event is stored before parsing (lossless original).
- **File Collector** — streams files line by line (flat memory), path-traversal protected,
  per-line format hints, upload tracking in `ulpf-uploads`.
- **Source Registry API** — `POST /sources`, `GET /sources`, `GET /sources/{id}`.
- **Upload jobs** — `POST /sources/{id}/upload` (chunked, size-limited), `GET /uploads/{id}`,
  `GET /sources/{id}/uploads`.
- **Event API** — `GET /events` (filters), `GET /events/{id}` (with Bronze lineage),
  `GET /sources/{id}/events`.
- **DLQ read API** — `GET /dlq`, `GET /dlq/{id}` (inspect-only; no replay yet).
- **Source statistics** — `GET /sources/{id}/stats` plus a dashboard endpoint `GET /dashboard`.
- **UI** — dashboard, sources (list/new/detail + upload), uploads, events (+detail/u-lineage), DLQ.
- **Unit tests** — `python -m pytest tests/ -q` (38 tests, no live services required).

Module 3/4 (DLQ replay, parser versioning, AI parsers, K8s) are intentionally out of scope here.