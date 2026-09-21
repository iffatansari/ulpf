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