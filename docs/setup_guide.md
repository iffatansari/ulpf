# ULPF Setup Guide

Covers the full stack: real-time UDP/HTTP/SSE ingestion plus file ingestion,
Bronze persistence, Source Registry, and UI.
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
| UI                  | 3000        | React dashboard (express + normalizer + `/backend` proxy to the API) |
| API                 | 8000        | Source Registry, uploads, events…   |
| HTTP Log Collector  | 8081        | HTTP ingestion                      |
| Syslog Collector    | 1514/udp    | Syslog ingestion                    |
| SSE Collector       | none        | Optional `sse` profile; upstream pull only |
| File Collector      | 8082 (internal) | Module 2 file processing        |
| OpenSearch          | 9200        | bronze / silver / dlq indices       |
| Redpanda            | 9092        | logs.raw / logs.normalized / logs.dlq|

The UI page is served alongside a same-origin proxy: any `/backend/*` path on
port 3000 is forwarded to the FastAPI service (`BACKEND_URL`, default
`http://localhost:8000`). So the Sources / Events / DLQ / Dashboard pages read
the real pipeline data without any CORS setup, and the UI container only needs
a single published port.

## 4. Confirm the pipeline is healthy

```bash
curl http://localhost:8000/dashboard
curl http://localhost:8001 2>/dev/null   # (file collector is internal-only; not needed)
```

`/dashboard` should return
`{"sources": N, "normalized_events": N, "dlq_events": N, "uploads": N, ...}`.

### 4.1 Check normalization without Docker

`python demo/stream_normalization_lab.py` runs a local SSE upstream, the real
collector, and the real parsers in one process, then prints the Bronze → Silver
result for syslog, CEF, JSON, multi-line, unknown, and `[DONE]` frames. Add
`--crlf` to switch the upstream to CRLF framing or `--max-event-bytes 160` to
watch oversized frames get dropped.

### 4.2 Severity mapping

Silver `severity` is always one of `low`, `medium`, `high`, `critical`, and
every parser resolves into that same vocabulary, so a query written against one
source works against another. The mapping lives in
`orchestrator/parsers/severity.py`:

- Syslog `<PRI>` is authoritative when present (`34` = auth/crit → `critical`);
  free-text keyword inference in the message is only the fallback for lines
  without a `<PRI>`, and the most severe keyword wins.
- Numeric JSON `level`/`severity` uses the syslog 0-7 scale, where lower is
  worse: 0-2 → `critical`, 3 → `high`, 4 → `medium`, 5-7 → `low`. Values
  outside 0-7 fall back to text matching.
- CEF keeps its own 0-10 scale (higher is worse): 8-10 → `critical`,
  5-7 → `high`, 3-4 → `medium`, 0-2 → `low`. A vendor that sends a label
  instead of a number (`High`) is understood, and an unrecognized value stays
  `medium`.
- Text tokens are shared: `emerg`/`panic`/`fatal`/`alert`/`crit`/`critical` →
  `critical`; `err`/`error`/`severe`/`failure` → `high`; `warn`/`warning` →
  `medium`; `notice`/`info`/`debug`/`trace` → `low`; anything unrecognized →
  `low`.

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

Valid `transport` values: `udp`, `http`, `file`, `sse`, `other`.
Valid `source_type` values: `network_device`, `server`, `application`,
`database`, `cloud`, `iot`, `custom`.

Remove a source with `curl -X DELETE http://localhost:8000/sources/<source_id>`
(the UI's "Remove" button calls exactly this endpoint).

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

## 6. Real-time SSE ingestion

Register the upstream feed as an SSE source and use the returned ID for the collector:

```bash
curl -s -X POST http://localhost:8000/sources \
  -H "Content-Type: application/json" \
  -d '{"name":"Application Events","source_type":"application","transport":"sse","expected_format":"json"}'
```

Set `UPSTREAM_SSE_URL`, `SSE_SOURCE_ID`, and `SSE_ALLOWED_HOSTS` in the shell or an untracked environment file. Set `SSE_BEARER_TOKEN` only when the upstream requires bearer authentication, then start the opt-in profile:

```bash
docker compose --profile sse up --build -d sse_collector
```

The collector validates the target URL and response type, follows SSE framing across network chunks, resumes with `Last-Event-ID`, reconnects on transient failures, and sends data frames to `logs.raw`. The orchestrator applies the same JSON, CEF, or syslog parsers used by other transports. Normalized events reach the existing UI through `GET /events/stream?source_id=<source_id>`.

For JSON data, include a parseable `time`, `timestamp`, or `@timestamp` field. `SSE_FORMAT_HINT=auto` detects JSON, CEF, and syslog payloads; invalid or unmapped data goes through the existing DLQ path.

Configuration reference:

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `UPSTREAM_SSE_URL` | yes | – | `http(s)` SSE endpoint; credentials in the URL are rejected |
| `SSE_ALLOWED_HOSTS` | yes | – | Comma-separated allowlist; collection fails closed for any other host |
| `SSE_SOURCE_ID` | no | `sse-source-1` | Must match the registered source ID |
| `SSE_SOURCE_TYPE` | no | `application` | `application`, `cloud`, `custom`, `database`, `iot`, `network_device`, `server` |
| `SSE_BEARER_TOKEN` | no | – | Sent as `Authorization: Bearer …`; requires HTTPS |
| `SSE_ALLOW_INSECURE_HTTP` | no | `false` | Explicit opt-in to send a bearer token over HTTP |
| `SSE_FORMAT_HINT` | no | `auto` | `auto`, `cef`, `json`, `syslog`, `unknown` |
| `SSE_MAX_EVENT_BYTES` | no | `262144` | Per-line and per-frame size ceiling; maximum `1000000` |
| `SSE_MAX_WIRE_BYTES` | no | `900000` | Serialized envelope ceiling, maximum `900000`; larger events are skipped instead of failing the stream |
| `SSE_CONNECT_TIMEOUT_SECONDS` | no | `10` | Must be finite and positive |
| `SSE_READ_TIMEOUT_SECONDS` | no | `60` | Must be finite and positive |
| `SSE_RECONNECT_INITIAL_SECONDS` | no | `1` | Client backoff floor |
| `SSE_RECONNECT_MAX_SECONDS` | no | `30` | Client backoff ceiling |
| `SSE_RETRY_MAX_SECONDS` | no | `300` | Ceiling for an upstream `retry` value |
| `SSE_COLLECTOR_ID` | no | `sse-collector-1` | Recorded on every envelope |
| `RAW_TOPIC` | no | `logs.raw` | Kafka topic for raw envelopes |
| `REDPANDA_BROKER` | no | `redpanda:29092` | Kafka bootstrap address |

Behavior notes:

- `SSE_ALLOWED_HOSTS` is mandatory; a missing or mismatched value stops the collector instead of connecting to an arbitrary host.
- Bearer tokens require HTTPS. Set `SSE_ALLOW_INSECURE_HTTP=true` only for an HTTP-only upstream you control.
- Redirects are not followed, so a redirecting upstream is treated as a configuration error.
- Reconnects use client-side exponential backoff with a `0.1s` floor; an upstream `retry` value raises the floor of that backoff and is capped by `SSE_RETRY_MAX_SECONDS`.
- `[DONE]` payloads are published to `logs.raw` as normal events; the collector keeps consuming until the upstream closes the stream.
- Each received record gets a unique `event_id`, so a reused upstream SSE `id` never overwrites an earlier Bronze document. The upstream id is preserved in `transport_metadata.sse_event_id`, and per the SSE specification an id stays in effect for later events; inherited ids are used for `Last-Event-ID` resumption but are not re-attributed to events that did not declare one.
- Resume IDs are kept in memory. After a restart the collector reconnects without `Last-Event-ID`, so the upstream may replay events.
- Unterminated events are discarded at stream end. Frames above `SSE_MAX_EVENT_BYTES`, envelopes above `SSE_MAX_WIRE_BYTES`, and envelopes the broker rejects as too large are skipped rather than failing the stream. The first skip per connection is logged with a count on each reconnect.
- `SIGTERM` and `SIGINT` stop the stream gracefully and close the Kafka producer.
- Python dependencies are pinned to exact versions in `collectors/sse_collector/requirements.txt`. The base image `python:3.11-slim` is still a mutable tag; pin it by digest before promoting this collector to a production deployment.
- Host matching is exact and fails closed, but an allowlisted host may legitimately resolve to a private, loopback, or link-local address. Keep `SSE_ALLOWED_HOSTS` as narrow as possible, and note that `SSE_BEARER_TOKEN` supplied through `.env` is readable by anyone who can read the container environment — use a mounted secret file on shared hosts.

---

## 7. UI

Open http://localhost:3000.

The UI is a self-contained React SPA (Vite + Express) that ships its own
multi-format log normalizer (`POST /api/normalize`) and runs on a single port.

- **Dashboard** — quick normalize + summary.
- **Events** — Ingest Events (paste/upload logs to normalize), Normalized Events
  (OCSF output), DLQ (Failed Events).
- **Sources** — Log Sources list and Add Source (format detection + parser
  suggestions, collector snippets).
- **Parsers** — Multi-Parser Chain config, Drain3 (unsupervised), Custom Parsers.
- **Schema** — OCSF Schema and Schema Explorer.
- **Monitoring** — Metrics, Logs, System Health (polls `/api/ping`).

---

## 8. API reference

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
| GET    | `/events/stream`                  | Real-time normalized event stream     |
| GET    | `/events/{id}`                    | Normalized event + Bronze raw event  |
| GET    | `/dlq`                            | List DLQ records                     |
| GET    | `/dlq/{id}`                       | DLQ record + Bronze raw event        |

Interactive docs: http://localhost:8000/docs (FastAPI / OpenAPI).

---

## 9. Tests (no live services needed)

```bash
# Windows PowerShell
$env:PYTHONPATH = "orchestrator"
python -m pytest tests/ -q

# macOS / Linux
PYTHONPATH=orchestrator python -m pytest tests/ -q
```

The test suite covers File Collector streaming/format-hint/path-safety, SSE
parsing/streaming/reconnect boundaries, Bronze persistence helpers, source model
validation, API query construction, and offline end-to-end reconciliation through
real collector and orchestrator logic. All tests use fakes and do not require
Docker, OpenSearch, or Redpanda.

---

## 10. Postgres-agnostic note

Statistics are computed directly from OpenSearch (counts + aggregations);
there is no separate analytics database in Module 2.

---

## 11. Troubleshooting

- **`docker compose up` fails on the ui service** — ensure WSL/engine is running first.
- **No events after upload** — check File Collector logs:
  `docker compose logs file_collector` and `docker compose logs orchestrator`.
- **SSE collector does not start** — verify `UPSTREAM_SSE_URL`,
  `SSE_SOURCE_ID`, and `SSE_ALLOWED_HOSTS`, then inspect
  `docker compose --profile sse logs sse_collector`. Common startup errors are
  `SSE_ALLOWED_HOSTS must list at least one upstream host`, `upstream host is
  not allowed`, and `HTTPS is required when SSE_BEARER_TOKEN is set`.
- **SSE events appear in Bronze but not Silver** — inspect
  `docker compose --profile sse logs orchestrator` and query `/dlq`; JSON feeds
  need a valid timestamp and expected data fields.
- **`/uploads/{id}` stuck on `processing`** — the orchestrator or File Collector
  may be down; both must be healthy for a job to finish.
- **File too large** — `MAX_UPLOAD_MB` (default 100) is configurable via the api
  service environment.