# ULPF — Universal Log Pre-Processing Framework

A containerized framework that collects logs from multiple sources, processes them through a shared pipeline, normalizes events, and prepares them for analysis.

**Pipeline:** `Log Source → Collector → Redpanda → Normalizer → OpenSearch`

## Quick Start

Prerequisites: Git, Docker Desktop

```bash
git clone https://github.com/iffatansari/ulpf.git
cd ulpf
docker compose up --build -d
docker compose ps   # all services Up; Redpanda/OpenSearch Healthy
```

Apps:
- UI: http://localhost:3000
- API: http://localhost:8000
- HTTP Log Collector: http://localhost:8081

## File Ingestion & Source Onboarding

Adds file-based log onboarding to the pipeline. Every raw event is persisted losslessly in **Bronze** before parsing, so the original payload can always be traced to any normalized event or DLQ record.

**Flow:** `Source Registry API → File Collector → logs.raw (Redpanda) → OpenSearch (bronze / silver / dlq)`

Register a file source and upload:

```bash
# Register a source (transport must be "file")
curl -s -X POST http://localhost:8000/sources -H "Content-Type: application/json" \
  -d '{"name":"FW Logs","source_type":"network_device","transport":"file","expected_format":"mixed"}'

# Upload a log file (returns upload_id; job runs in background)
curl -s -X POST http://localhost:8000/sources/<source_id>/upload \
  -F "file=@demo/mixed_security_logs.log"

# Poll the upload job until completed
curl -s http://localhost:8000/uploads/<upload_id>
```

### Features
- Bronze persistence: every raw event stored before parsing
- File Collector: line-by-line streaming, path-traversal protected, per-line format hints, upload tracking
- Source Registry API and upload jobs
- Event API with Bronze lineage; DLQ read API; source statistics + dashboard
- UI dashboard, sources, uploads, events, DLQ
- Unit tests: `python -m pytest tests/ -q`

## Real-Time SSE Ingestion

The SSE collector consumes a Server-Sent Events endpoint continuously, sends each event through `logs.raw`, and lets the existing orchestrator normalize it in real time.

**Flow:** `SSE source → SSE Collector → logs.raw → Bronze → Silver / DLQ → /events/stream`

Register the source and note the returned `source_id`:

```bash
curl -s -X POST http://localhost:8000/sources -H "Content-Type: application/json" \
  -d '{"name":"Application Events","source_type":"application","transport":"sse","expected_format":"json"}'
```

Configure the collector and start its optional Compose profile:

```bash
export UPSTREAM_SSE_URL="https://events.example.com/stream"
export SSE_SOURCE_ID="<source_id-from-registration>"
export SSE_ALLOWED_HOSTS="events.example.com"
export SSE_BEARER_TOKEN="<token-if-required>"

docker compose --profile sse up --build -d sse_collector
```

`SSE_ALLOWED_HOSTS` is required and fails closed when the upstream host is not listed. Bearer authentication requires HTTPS unless `SSE_ALLOW_INSECURE_HTTP=true` is set explicitly for an HTTP-only upstream. `[DONE]` payloads are ingested like any other event instead of ending collection.

For JSON feeds, each `data` payload should contain a valid timestamp (`time`, `timestamp`, or `@timestamp`) so the JSON parser can produce a normalized event. The collector supports comments and heartbeats, multiline `data` fields, upstream event IDs, `Last-Event-ID` resume headers, `retry` controls, bounded reconnects, bearer authentication, and bounded event sizes. Resume IDs are held in memory, so a collector restart replays from the beginning. The existing UI receives normalized records through `/events/stream` without a second integration.

## Simulated Live Stream (no upstream required)

Transport `kafka_sim` publishes synthetic events straight into `logs.raw`, so you can exercise the full pipeline — raw ingestion, normalization, and DLQ handling — without a real SSE endpoint. It is a demo source, not a collector: the browser cannot start it, a terminal can.

**Flow:** `kafka_sim source → live_sim → logs.raw → Bronze → Silver / DLQ → /events/stream`

The one thing that must line up is `SIM_SOURCE_ID` in `.env`, which has to equal the `source_id` the API assigned your source. If it drifts, the producer keeps emitting but every event is attributed to an id that is not in the registry, and the source page silently shows zeros.

`demo/sim_source.py` handles that for you — it finds the source by name, creates it if missing, writes `.env`, starts the container, and waits for the first events:

```bash
python demo/sim_source.py --name "Simulated SOC feed" --start
```

Useful flags: `--list` shows every registered source with its id, and omitting `--start` only reconciles `.env`.

Manually, the equivalent is:

```bash
docker compose --profile sim up -d --force-recreate live_sim
```

`SIM_RATE` controls events per second, and `SIM_FORMAT` (default `syslog,cef,json,unknown`) selects the generated mix. The default mix is DLQ-free: all four payloads normalize, so the whole live stream reaches Silver and the DLQ stays empty. Three of them map straight onto the primary parsers; the fourth is free-form prose that only `drain3-fallback-v1` can handle, so the live stream exercises that tier too — a recognizable identity is recoverable from it, which is why it lands in Silver at low severity rather than in the DLQ.

To see the DLQ populated on purpose, add `malformed_json` to the mix. Those records declare a `json` hint and fail parsing on purpose:

```bash
SIM_FORMAT=syslog,cef,json,malformed_json docker compose --profile sim up -d --force-recreate live_sim
```

The one flag that intentionally breaks Silver indexing is `--string-levels`: Silver maps `extensions.original_json.level` from the first document it sees, so emitting `level` as a string after a numeric one has been indexed makes OpenSearch reject the whole document. It is for testing index rejection, never for the live stream.