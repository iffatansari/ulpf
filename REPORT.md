# ULPF — Module 2 Report

## What was built

Module 2 ("real-world log files on a real pipeline") on top of the Module 1 baseline.
Scope is deliberately Module 2 only — no DLQ replay, parser versioning, AI parsers
or Kubernetes (Modules 3/4).

### 1. Bronze layer (lossless, before parsing)
- `orchestrator/main.py` now persists every raw event to `ulpf-bronze` **before**
  parser selection:
  `opensearch.index(index=BRONZE_INDEX, id=raw_event.event_id, body=raw_document)`.
- Bronze has an explicit mapping (`BRONZE_MAPPING`) with keyword fields for
  lineage attributes: `source_id`, `transport`, `format_hint`, `bronze_uri`,
  `collector_id`, `envelope_schema_version`, `upload_id`.
- `upload_id` is read from a Kafka header attached by the File Collector and stored
  additively on the Bronze doc — the `RawEventEnvelope` schema is untouched.
- The DLQ/Silver records keep their existing `raw_event_id` → Bronze linkage.

### 2. File Collector (`collectors/file_collector`)
- `POST /process` (internal) accepts an upload job; validates the path stays inside
  the controlled `/data/uploads` directory (path-traversal safe).
- Streams files line-by-line — memory stays flat regardless of file size.
- Per-line format hints only (`cef` / `syslog` / `json` / `unknown`); the
  orchestrator remains the parsing authority.
- Wraps every non-blank line in `RawEventEnvelope(transport="file")` and publishes
  to `logs.raw` with an `upload_id` header.
- Isolates per-line failures (counted as `line_errors`, never aborts the file).
- Tracks job state in `ulpf-uploads` (`queued → processing → completed/failed`).

### 3. API layer (`api/`)
- **Source Registry** — `POST /sources` (validated Literals for transport/source_type),
  `GET /sources`, `GET /sources/{id}`, `PUT /sources/{id}` (update mutable fields —
  e.g. disable a source); persistence in `ulpf-sources`.
- **Uploads** — `POST /sources/{id}/upload`: validates source exists/enabled/file
  transport; streaming chunked save with `MAX_UPLOAD_MB` cap (413 on overflow);
  empty files rejected (400); records the job, updates source timestamps, hands off
  to the File Collector in the background.
  `GET /uploads/{id}` and `GET /sources/{id}/uploads`; **every** read derives and
  persists exact Bronze/Silver/DLQ counts via `upload_event_counts` (scroll on
  Bronze, chunked terms-count on Silver/DLQ — exact for any upload size), so counts
  converge as the orchestrator drains instead of freezing at "collector done".
- **Events** — `GET /events` (source_id/severity/parser_id/start/end filters),
  `GET /events/{id}` (normalized + Bronze raw lineage), `GET /sources/{id}/events`.
- **DLQ (read-only)** — `GET /dlq`, `GET /dlq/{id}` with Bronze lineage. No replay
  (that is Module 3).
- **Stats** — `GET /sources/{id}/stats` (raw/normalized/DLQ counts, format and
  parser distributions, `last_ingested_at`, success rate, upload blank/error lines)
  and `GET /dashboard`.

### 4. UI (`ui/`, Vite + React 18 + Express 5 + TypeScript)
- React SPA with an integrated Express API on one port (3000). Includes a
  built-in multi-format log normalizer (`POST /api/normalize`): auto, JSON,
  syslog, CEF, LEEF, key=value, Apache and plain-text parsing with
  OCSF 1.3.0 classification.
- **Backend integration** — the Express server exposes a same-origin proxy at
  `/backend/*` (forwarded to `BACKEND_URL`, default `http://localhost:8000`).
  The Sources registry is backed by the FastAPI Source Registry (create /
  update / **delete**), and the Dashboard feeds, Normalized Events, DLQ and
  Source detail pages read live data from the API (stats, per-source events,
  per-source DLQ). When the backend is unreachable the UI falls back to its
  local store so the workspace keeps working.
- Pages: Dashboard, Sources (list / new / detail), Events (ingest / normalized /
  DLQ), Parser configurations (incl. Drain3 and custom parsers), OCSF schema
  explorer, Monitoring (metrics / logs / system health), plus a 404 catch-all.
- Containerized via `ui/Dockerfile`; built with `pnpm build` and served with
  `node dist/server/node-build.mjs`. `pnpm build`, `pnpm typecheck` and
  `pnpm test` are clean.

### 5. Deployment
- `docker-compose.yml`: new `file_collector` service; `./data/uploads` bind mount
  shared by the api and the File Collector; Module 2 env vars added to the api.
- `.gitignore` now excludes the runtime upload volume (`data/`).

---

## Verification performed

### Offline (unit/integration, no live services)
| Check | Result |
|-------|--------|
| `python -m pytest tests/ -q` | **45 passed** — incl. the 2 pre-existing Module 1 end-to-end tests |
| New tests: File Collector streaming/format/path-safety/error-isolation/blank-only failure | pass (fakes, no Docker) |
| New tests: Bronze helper (`ensure_index`, `extract_upload_id`, mapping) | pass |
| New tests: API helpers (model validation, event query, upload counts, source update) | pass |
| New test: `tests/test_pipeline_reconciliation.py` — full offline E2E running `demo/mixed_security_logs.log` through the real File Collector + orchestrator logic (faked Kafka/OpenSearch): 3996 non-blank lines → 3996 Bronze → 3585 Silver + 411 DLQ, format distribution (syslog 1953 / json 1014 / cef 618 / unknown 411), lineage round-trip | pass |
| `docker compose config --quiet` | OK |

### Live end-to-end (stack running via Docker Desktop)
The Docker engine was brought up and the full stack started:
`docker compose up --build -d` → api (8000), ui (3000), opensearch (9200, healthy),
redpanda (9092, healthy), syslog-collector (1514/udp), http-json-collector (8081),
file-collector (internal 8082), orchestrator — all `Up`.

| Check | Result |
|-------|--------|
| `GET /dashboard` on a fresh stack | 200 — `{"sources":0,...}` |
| Register `Demo Security File` source (transport=file, format=mixed) | 201, id `demo-security-file-07b2b9` |
| Upload `demo/mixed_security_logs.log`; poll `GET /uploads/{id}` | `completed` — total_lines=4000, blank_lines=4, published_events=3996, line_errors=0, **raw_events=3996 = normalized_events(3585) + dlq_events(411)** |
| Reconciliation vs `ulpf-bronze` count | bronze 3996, silver 3585, dlq 411 — exact |
| Format distribution (Bronze agg) | syslog 1953, json 1014, cef 618, unknown 411 — matches generator seed |
| Lineage: `GET /events/{silver_id}` | returns normalized + verbatim Bronze raw payload |
| Lineage: `GET /dlq/{dlq_id}` | returns DLQ record (`parsers_attempted` cef→json→syslog, `format_unidentified`) + raw Bronze |
| `GET /sources/{id}/stats` | raw 3996 / normalized 3585 / dlq 411, formats + parsers breakdown, success_rate 89.71 |
| `GET /events` filters (source_id/parser_id/severity) | correct filtered totals (e.g. json-parser-v1 & severity=low → 1014-ish subset) |
| Small mixed file with 1 blank + 1 garbage line | 5 lines → 1 blank, 4 published → 3 normalized + 1 DLQ, `line_errors=0` — per-line isolation |
| Upload to unknown source | 404 `source not found` |
| Upload to disabled source (`PUT /sources/{id}` then POST) | 400 `source is disabled` |
| Upload with no file / malformed body | 422 missing `file` |
| Blank-only and empty files | status `failed`, `failure_reason="no non-blank lines to process"` |
| Oversized upload (150 MB > MAX_UPLOAD_MB=100) | HTTP 413 + failed job `file_too_large (max 100 MB)` |
| Module 1 regression — UDP 1514 (syslog + CEF + unknown) | 3 Bronze (udp); syslog+CEF → Silver, unknown → DLQ |
| Module 1 regression — HTTP JSON 8081 (valid + malformed) | valid → `ingested:2` → 2 Silver; malformed → 400 `invalid_json`, preserved to Bronze → DLQ `no_parser_match` |
| UI (Next.js) | `/`, `/sources`, `/sources/{id}`, `/upload/`…, `/events`, `/dlq` all 200 with live data |

Expected demo numbers confirmed: `total_lines=4000, blank_lines=4, published_events=3996, raw_events=3996`.

### Defects found and fixed during live verification
1. **Fresh-stack 500 on `/dashboard`, `/events`, `/dlq`.** OpenSearch rejects a `sort`
   on a field with no mapping; empty dynamic-mapped Silver/DLQ had no `time` /
   `first_seen_at` mapping. Fixed by declaring minimal date mappings for the sort
   fields in `orchestrator/main.py` (`SILVER_MAPPING`/`DLQ_MAPPING`) and `api/db.py`,
   while keeping everything else dynamic (`.keyword` queries unchanged).
2. **File Collector crash on first job.** `OpenSearch.update()` does not accept a
   `doc_as_upsert` kwarg; every `patch_upload` raised `TypeError` after the API had
   already marked the job queued (upload stuck forever). Fixed with a
   `body={"doc": fields, "upsert": fields}` update in `collectors/file_collector/main.py`.
3. **Stale upload counts.** The collector marks `completed` the moment publishing
   ends, but the orchestrator still needs time to persist 3996 Bronze docs — `GET
   /uploads` showed 1146/1031/115 instead of 3996/3585/411 and the UI stopped
   polling at `completed`. Fixed: `GET /uploads/{id}` now derives + persists exact
   counts on every read, lists/dashboard merge them via `enrich_upload_counts`, and
   the upload page polls until `raw_events >= published_events`.
4. **No way to disable a source.** Source Registry was create/list/read only; the
   `enabled` guard on upload was unreachable via the API. Added
   `PUT /sources/{source_id}` (mutable: name/description/expected_format/enabled).

Notably *not* broken (regression clean): Module 1 UDP/HTTP ingestion and the whole
existing offline suite stayed green.

## Known limitations (live)
- Upload `completed` means "all lines published"; exact Silver/DLQ counts converge
  shortly after as the orchestrator drains — the UI polls until they settle. No push
  event exists yet.
- The HTTP JSON collector only attempts the `json` parser regardless of a body whose
  `events` JSON is malformed at the *object* but not document level; payloads it
  rejects still reach DLQ verbatim (never dropped).
- `last_seen_at`/`last_upload_at` on a source are stamped by the upload pipeline, not
  by a scheduler (there is no heartbeat collector yet — future module).
- The 100 MB `MAX_UPLOAD_MB` cap is per-file and enforced while streaming; it is
  configurable via env only.

## Design decisions worth noting

- **Bronze before parse.** Parsing errors must never destroy the original; the
  DLQ's `raw_event_id` points at Bronze, and the UI drills from any normalized or
  DLQ record to the verbatim raw payload.
- **`.keyword` suffix discipline.** Bronze uses an explicit mapping, Silver/DLQ keep
  dynamic mappings — so API queries address Silver/DLQ string fields as
  `extensions.source_id.keyword`, `metadata.source_id.keyword`,
  `raw_event_id.keyword`, `parser_id.keyword`, `severity.keyword`.
- **Decoding boundary.** File bytes are decoded UTF-8 with `errors="replace"`; the
  Module 2 lossless guarantee applies to the decoded line (see
  `collectors/file_collector/main.py` docstring).
- **No new core schemas.** `RawEventEnvelope`/`NormalizedEvent`/`DLQRecord` are
  unchanged; everything Module 2-specific rides on separate indices and Kafka headers.
- **Exact counts, not estimates.** Per-upload counts are computed from Bronze
  event ids via terms-count on Silver/DLQ `raw_event_id`.
- **One place for source identity.** The Source Registry is the single source of
  truth; collectors receive `source_id` in the job payload, never invent it.

## Out of scope (explicitly deferred)
DLQ reprocessing/replay and parser promotion (Module 3), AI-assisted parsers and
schema versioning (Module 3), Kubernetes deployment (Module 4).