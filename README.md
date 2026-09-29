# ULPF — Universal Log Pre-Processing Framework

A containerized framework that collects logs from multiple sources, normalizes them through a shared pipeline, and stores them for analysis.

**Pipeline:** `Log Source → Collector → Redpanda → Normalizer → OpenSearch`

## Quick Start

```bash
git clone https://github.com/iffatansari/ulpf.git
cd ulpf
docker compose up --build -d
```

UI: http://localhost:3000 · API: http://localhost:8000

## What's in this branch

**File Ingestion & Source Onboarding (#4, #7)** — We added file-based log onboarding. Raw events are stored losslessly in Bronze before parsing, so any normalized event traces back to its original payload, backed by a source registry API and a line-by-line file collector.

**Live UI Integration (#6)** — We added a real backend-connected UI. A React + TypeScript SPA served through an Express proxy reads live sources, events, and DLQ data from the API, with a built-in normalizer for JSON, syslog, CEF, and LEEF payloads.

**Drain3 in the UI (#8)** — We added real Drain3 template mining to the UI, replacing the in-browser heuristic with the orchestrator's own miner so clustering has one implementation.

**DLQ Reprocessing, SSE Feed, and Parser Registry (#9, #10)** — We added replay for dead-lettered events with a dry-run preview, a live SSE feed of normalized records, a parser registry with custom parsers and version rollback, and a shared `/stats` aggregate so the dashboard and metrics counters cannot disagree. The syslog parser was also rewritten to RFC 5424.

**Production Compose Overlay (#11)** — We added a production overlay with health checks, memory limits, loopback-pinned broker and ingestion ports, and a configurable data directory, keeping deployment concerns out of the local compose file.
