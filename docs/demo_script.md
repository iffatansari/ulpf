# ULPF Module 2 — Demo Script

A ~5 minute end-to-end demonstration of file ingestion, Bronze persistence,
normalization, DLQ and the UI. Uses the included demo file
`demo/mixed_security_logs.log` (4,000 lines mixing syslog / JSON / CEF /
unstructured, with a few deliberate blank lines).

Prerequisite: the stack is running (`docker compose ps` all Up).

---

## 0. Baseline (Module 1 regression check)

```bash
curl -s http://localhost:8000/dashboard
```
Each numbered run produces:
1. **Source registration** — `POST /sources`, transport must be `file`.
2. **File upload** — `POST /sources/{id}/upload`, returns `upload_id`.
3. **Upload tracking** — poll `GET /uploads/{id}` until `completed`.
4. **Pipeline statistics** — `GET /sources/{id}/stats`.
5. **UI walk-through** — Sources → Events → DLQ.

---

## 1. Register a file source

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

Capture the `source_id`, e.g. `firewall-logs-1a2b3c` — used below.

**Demo talking point:** the Source Registry validates transport/source_type.
Try `"transport": "syslog"` and show it is rejected (only `udp`, `http`, `file`,
`other` are valid).

---

## 2. Upload the demo log

```bash
curl -s -X POST http://localhost:8000/sources/firewall-logs-1a2b3c/upload \
  -F "file=@demo/mixed_security_logs.log"
```

Returns `202` immediately:

```json
{"upload_id": "….", "source_id": "firewall-logs-1a2b3c", "filename": "mixed_security_logs.log", "status": "queued"}
```

**Demo talking point:** this is a streaming path — the API stores the file in
the shared `/data/uploads` volume in 1 MiB chunks, then hands it to the File
Collector. The browser request returns instantly; the pipeline runs async.

---

## 3. Track the job

```bash
curl -s http://localhost:8000/uploads/<upload_id>
```

Watch it move `queued → processing → completed`. `GET /uploads/{id}` derives
counts from OpenSearch on every read, so `raw_events` converges to `3996`
a moment after `completed` as the orchestrator finishes writing Bronze — the
UI polls until `raw_events >= published_events`. On completion:

- `total_lines` = 4000
- `blank_lines`  = 4          (deliberate)
- `published_events` = 3996   (every non-blank line published to logs.raw)
- `raw_events` = 3996         (Bronze count — persisted BEFORE parsing)
- `normalized_events` + `dlq_events` = 3996   (exact reconciliation)

**Demo talking point:** every raw event is in Bronze (lossless) even if the
parser later rejects it, and the numbers reconcile exactly — raw = normalized + DLQ.

---

## 4. Stats

```bash
curl -s http://localhost:8000/sources/firewall-logs-1a2b3c/stats
```

Shows format distribution (syslog / json / cef / unknown), parser distribution,
`success_rate`, `last_ingested_at`, upload-derived `blank_lines`/`line_errors`.

**Demo talking point:** statistics are computed live from OpenSearch — no second
database, no manual bookkeeping.

---

## 5. UI walk-through

Open http://localhost:3000.

1. **Dashboard** — total sources, raw/normalized/DLQ events.
2. **Sources** — find *Firewall Logs*; open its detail page.
   - Stats card: 3,996 raw, ~3,8xx normalized, small DLQ count, ~98% success.
   - Upload a different file from the page itself.
3. **Events** — filter by source id; open any event.
   - *Traceability*: parser id, tier, confidence, schema version.
   - *Bronze Raw Event*: the exact original payload, verbatim.
4. **DLQ** — records that failed normalization: click one to see the original
   raw line in Bronze and the failure metadata.

**Demo talking point:** full traceability — from a normalized event (or a DLQ
record) you can always drill back to the untouched raw payload in Bronze.

---

## 6. Optional: nobody said it had to be pretty

Generate a variant demo file:

```bash
python demo/generate_mixed_log.py 8000        # bigger
python demo/generate_mixed_log.py 500         # smaller / faster
```

Re-upload it and watch the numbers update.

---

## Expected result summary

| Metric            | Expected value        |
|-------------------|-----------------------|
| total_lines       | 4000                  |
| blank_lines       | 4                     |
| published_events  | 3996                  |
| raw_events        | 3996                  |
| normalized + DLQ  | 3996 (= raw_events)   |