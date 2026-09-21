# ULPF Setup Guide

Covers the full stack: Module 1 (real-time UDP/HTTP ingestion) plus
Module 2 (file ingestion, Bronze persistence, Source Registry, UI).
Designed for Docker Desktop on Windows/macOS/Linux.

---

## 1. Prerequisites

- Git
- Docker Desktop (with the engine running)
- Node.js 18+ (only needed if you want to lint/build the UI outside Docker)

---

## 2. Clone and start

```bash
git clone https://github.com/iffatansari/ulpf.git
cd ulpf
docker compose up --build -d
```

First start pulls OpenSearch, Redpanda and builds the collector/orchestrator/
api/ui images. It takes a few minutes.

## 3. Verify the stack

```bash
docker compose ps
```

Expected: all services **Up**; `opensearch` and `redpanda` **Healthy**.

| Service             | Port        | Purpose                             |
|---------------------|-------------|-------------------------------------|
| UI                  | 3000        | Next.js dashboard                   |
| API                 | 8000        | Source Registry, uploads, events…   |
| HTTP Log Collector  | 8081        | Module 1 HTTP ingestion             |
| Syslog Collector    | 1514/udp    | Module 1 syslog ingestion           |
| File Collector      | 8082 (internal) | Module 2 file processing        |
| OpenSearch          | 9200        | bronze / silver / dlq indices       |
| Redpanda            | 9092        | logs.raw / logs.normalized / logs.dlq|

## 4. Confirm the pipeline is healthy

```bash
curl http://localhost:8000/dashboard
curl http://localhost:8001 2>/dev/null   # (file collector is internal-only; not needed)
```

`/dashboard` should return
`{"sources": N, "normalized_events": N, "dlq_events": N, "uploads": N, ...}`.

---

## 5. Module 2: on-board a file source

### 5.1 Register the source

```bash
curl -s -X POST http://localhost:8000/sources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Firewall Logs",
    "source_type": "network_device",
    "transport": "file",
    "expected_format": "mixed"
  }'
```

Note the returned `source_id` (format: `<slug>-<6 hex chars>`).

Valid `transport` values: `udp`, `http`, `file`, `other`.
Valid `source_type` values: `network_device`, `server`, `application`,
`database`, `cloud`, `iot`, `custom`.

### 5.2 Upload a log file

The demo asset is included:

```bash
curl -s -X POST http://localhost:8000/sources/<source_id>/upload \
  -F "file=@demo/mixed_security_logs.log"
```

Returns `202` immediately with `upload_id`; processing runs in the background.

### 5.3 Track the job

```bash
curl -s http://localhost:8000/uploads/<upload_id>
```

While running the status is `processing`; when publishing finishes it is
`completed`. `GET /uploads/{id}` derives exact counts from OpenSearch on every
read, so `raw_events`, `normalized_events` and `dlq_events` converge to their
final values a moment after `completed` (the orchestrator drains the Kafka
backlog). Stop polling when `raw_events >= published_events`; the expected
invariant is `raw_events == normalized_events + dlq_events`. Other fields:
`total_lines`, `blank_lines`, `line_errors`.

### 5.4 Inspect results

```bash
curl -s http://localhost:8000/sources/<source_id>/stats     # pipeline stats
curl -s "http://localhost:8000/events?source_id=<source_id>"  # normalized events
curl -s http://localhost:8000/dlq                            # DLQ records
curl -s "http://localhost:8000/events/<event_id>"           # event + Bronze lineage
```

---

## 6. UI

Open http://localhost:3000.

- **Dashboard** — pipeline health, source/event counts.
- **Sources** — list, create, per-source detail with stats and file upload.
- **Uploads** — live job progress (auto-refreshing).
- **Events** — filterable list; event detail shows the Bronze raw event lineage.
- **DLQ** — records that failed to normalize (inspect-only).

---

## 7. API reference

| Method | Path                              | Description                          |
|--------|-----------------------------------|--------------------------------------|
| GET    | `/dashboard`                      | Global pipeline stats                |
| GET    | `/sources`                        | List sources                         |
| POST   | `/sources`                        | Register a source                    |
| GET    | `/sources/{id}`                   | Source detail                        |
| PUT    | `/sources/{id}`                   | Update name/description/format/enabled |
| GET    | `/sources/{id}/stats`             | Per-source pipeline statistics       |
| GET    | `/sources/{id}/events`            | Normalized events for the source     |
| GET    | `/sources/{id}/uploads`           | Upload jobs for the source           |
| POST   | `/sources/{id}/upload`            | Upload one log file (multipart)      |
| GET    | `/uploads/{id}`                   | Upload job + exact counts            |
| GET    | `/events`                         | List normalized events (filters)     |
| GET    | `/events/{id}`                    | Normalized event + Bronze raw event  |
| GET    | `/dlq`                            | List DLQ records                     |
| GET    | `/dlq/{id}`                       | DLQ record + Bronze raw event        |

Interactive docs: http://localhost:8000/docs (FastAPI / OpenAPI).

---

## 8. Tests (no live services needed)

```bash
# Windows PowerShell
$env:PYTHONPATH = "orchestrator"
python -m pytest tests/ -q

# macOS / Linux
PYTHONPATH=orchestrator python -m pytest tests/ -q
```

45 tests cover: File Collector streaming/format-hint/path-safety, Bronze
persistence helpers, the API helper logic (source model validation, source
update, event query building, upload count derivation), and a full offline
reconciliation run of `demo/mixed_security_logs.log` through the real File
Collector + orchestrator logic. All run against fakes — no Docker, OpenSearch
or Redpanda required.

---

## 9. Postgres-agnostic note

Statistics are computed directly from OpenSearch (counts + aggregations);
there is no separate analytics database in Module 2.

---

## 10. Troubleshooting

- **`docker compose up` fails on the ui service** — ensure WSL/engine is running first.
- **No events after upload** — check File Collector logs:
  `docker compose logs file_collector` and `docker compose logs orchestrator`.
- **`/uploads/{id}` stuck on `processing`** — the orchestrator or File Collector
  may be down; both must be healthy for a job to finish.
- **File too large** — `MAX_UPLOAD_MB` (default 100) is configurable via the api
  service environment.