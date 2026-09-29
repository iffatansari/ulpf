# Deploying ULPF to Render

Blueprint file: `render.yaml` (repo root).

## Root directory — the setting that breaks most first deploys

| Service | Root Directory | Why |
|---|---|---|
| `api` | *(leave empty — repo root)* | `api/Dockerfile` copies `api/`, `orchestrator/`, **and** `schema/` |
| `orchestrator` | *(leave empty — repo root)* | copies `orchestrator/` and `schema/` |
| `file_collector` | *(leave empty — repo root)* | copies `collectors/file_collector/` **and** `schema/` |
| `http_json-collector` | *(leave empty — repo root)* | copies `collectors/http_json_collector/` and `schema/` |
| `opensearch` | *(leave empty — repo root)* | build context is the repo root |
| `redpanda` | *(leave empty — repo root)* | build context is the repo root |
| `ui` | **`ui`** | built with `dockerContext: ./ui`; its Dockerfile is self-contained |

Setting Root Directory to `ui` for the backend services is the classic failure:
the build succeeds, then fails at `COPY schema` or `COPY orchestrator` with a
"file not found" error, because those directories do not exist under `ui/`.

When you create services manually in the dashboard instead of using the
blueprint, enter these values. The blueprint sets all of them for you.

## How to deploy

1. Push the branch containing `render.yaml`.
2. Render dashboard → **New → Blueprint**.
3. Select the repo and branch. Render detects `render.yaml` automatically.
4. Review the plans, then apply.

Or via the Render API/CLI:

```bash
render blueprints validate render.yaml     # Render CLI v2.7+
render blueprints launch render.yaml
```

## What gets created

| Service | Type | Plan | Disk | Public? |
|---|---|---|---|---|
| `opensearch` | private | 1c-2g | 10 GB | no |
| `redpanda` | private | 1c-2g | 10 GB | no |
| `api` | private | 0.5c-512mb | 10 GB | no |
| `file_collector` | private | 0.5c-512mb | — | no |
| `orchestrator` | worker | 0.5c-512mb | — | no |
| `http-json-collector` | web | 0.5c-512mb | — | **yes** |
| `ui` | web | 0.5c-512mb | — | **yes** |

Compute: **$92.50/mo**. Disks: **$7.50/mo** (30 GB @ $0.25/GB). Hobby workspace
adds $0. Roughly **$100/mo**, and it is billed continuously — there is no
scale-to-zero once a disk is attached.

Internal wiring is all literal, because private services resolve by name on
Render's private network:

- `OPENSEARCH_URL=http://opensearch:9200`
- `REDPANDA_BROKER=redpanda:9092`
- `FILE_COLLECTOR_URL=http://file_collector:8082`
- `BACKEND_URL=http://api:8000`

Because `api` is private, the browser never calls it directly — the `ui` Express
server proxies `/backend` server-side, so there is no CORS preflight in normal
use and the API is not internet-reachable.

## Omitted from this blueprint

**`syslog_collector`** — dropped. It listens on `1514/udp` and `5151/tcp`, and
Render only terminates HTTP(S). There is no way to expose a UDP or raw TCP port.
Run it on a host that accepts syslog, pointing `REDPANDA_BROKER` at an
externally reachable Redpanda.

**`sse_collector` and `live_sim`** — dropped. They are dev/simulation clients
that maintain long-lived SSE connections. They can be added as `worker`
services later if you need them.

## Known issues to verify on first deploy

### 1. Uploads will fail until the API sends bytes (blocking)

`api/routes/uploads.py` writes the file to its own `/data/uploads`, then POSTs
**only the path string** to `file_collector` (`notify_collector`, line ~264).
`file_collector` then reads that path from its own filesystem.

Render disks are per-service and cannot be shared, so on Render the collector
receives a path that does not exist in its container. Every upload will fail
with `file_collector_unavailable` or a file-not-found error.

Fix: change `notify_collector` to send the file as `multipart/form-data`, and
have `file_collector` write it to a local temp path before processing. The
persistent copy stays on the `api` disk. The collector's filesystem can stay
ephemeral because the in-flight file is only needed while it is processed.

This is a code change, not a config change, so it is not in `render.yaml`.

### 2. OpenSearch `vm.max_map_count` (cannot be fixed if it fails)

OpenSearch's bootstrap check requires `vm.max_map_count >= 262144`. That sysctl
is not namespaced, so it cannot be set from inside the container — it depends on
the Render host value. Render's own Elasticsearch guide assumes it is satisfied,
but that is verified for Elasticsearch, not OpenSearch.

If the `opensearch` service fails to start, check the logs for
`max virtual memory areas`. Workarounds, in order of preference: request a
different region, or move OpenSearch off Render.

### 3. Disk permissions for OpenSearch

The official image runs as the `opensearch` user (uid 1000) and writes to
`/usr/share/opensearch/data`. If the mounted disk is not writable by that uid,
add a `USER root` line and a `chown -R opensearch:opensearch` on that path in
`deploy/opensearch.Dockerfile`. Check the logs before assuming it is fine.

### 4. Private service port detection

Render detects the listening port of a private service and falls back to
`10000` if it cannot find one. `api` binds `0.0.0.0:8000` and
`file_collector` binds `0.0.0.0:8082`, so detection should work. If the UI
cannot reach the API, confirm the detected port in the service's settings.

No Dockerfile changes were needed for host binding: `api/Dockerfile` already
uses `--host 0.0.0.0`, `file_collector/main.py` calls
`uvicorn.run(host="0.0.0.0")`, and the UI's `app.listen(port)` binds all
interfaces by default.

## Security notes

- **`opensearch` is unauthenticated.** `plugins.security.disabled=true` is set
  for local convenience. It is a private service with no public URL, so it is
  not exposed. If you ever make it public, every index is readable by anyone.
- **`http-json-collector` is an unauthenticated public endpoint.** It is the log
  ingest path, so it must be reachable, but anyone who discovers the URL can
  write events into your pipeline. Put auth in front of it before using this
  with anything real.
- The `api` has no authentication. It is private, so this is contained, but
  there is nothing between Render's private network and your data.

## Why not one image

Render has no `docker-compose.yml` support, and a single container for all
services would need a process supervisor, would pin everything to one plan, and
would make any single component crash take down the whole stack. The blueprint
maps to Render's native service types instead.
