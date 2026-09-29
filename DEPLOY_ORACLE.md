# Deploying ULPF to Oracle Cloud Always Free

Target: a single Always Free Ampere A1 instance running the full compose stack.
Cost: **$0/month**, no card charged, no expiry.

This is the only free option that runs the stack **unmodified**. Render cannot
do this because it has no compose support, no shareable disk, and no UDP; the
per-service blueprint needs code changes to fix the upload path.

---

## What you get

| Resource | Always Free allowance |
|---|---|
| Ampere A1 compute (`VM.Standard.A1.Flex`) | **2 OCPU / 12 GB RAM** |
| Block volume | 200 GB total (47 GB minimum boot volume) |
| Public IPv4 | 1 |
| Inbound bandwidth | 10 TB/month |

**Read this before you start:** Oracle halved this tier on **June 15, 2026**, from
4 OCPU/24 GB to 2 OCPU/12 GB. A free-tier account that exceeds 2 OCPU or 12 GB
across all instances gets them **shut down**. Do not provision two instances.

---

## Step 1 — Sign up

1. Go to <https://www.oracle.com/cloud/free/> and create an account.
2. A credit card is required for a ~$1 pre-authorisation. It is not charged
   unless you upgrade to a paid account.
3. **Choose your home region carefully.** Always Free compute can only be
   provisioned in the home region, and you cannot change it later. Pick the
   region closest to you that also has free capacity — see step 2.
4. Complete the trial. The $300 / 30-day credit is irrelevant here; the Always
   Free resources are what you are using.

---

## Step 2 — Create the instance

Compute → **Instances** → **Create instance**.

| Field | Value |
|---|---|
| Placement | your home region |
| Image | **Canonical Ubuntu 24.04** (or 22.04) |
| Shape | **VM.Standard.A1.Flex** |
| OCPU | **2** |
| Memory | **12 GB** |
| Boot volume | 47 GB (default) |
| Public IPv4 | **assign a public IPv4 address** ✓ |
| SSH key | add your public key |

> If the shape dropdown does not show A1, the region is at capacity. See below.

### "Out of host capacity"

This is the single most common reason people give up on Oracle's free tier. The
message looks like an internal error and it means the region has no free ARM
capacity at that moment.

What actually works, in order:

1. **Retry.** Capacity frees up constantly. Try every few minutes.
2. **Change the fault domain.** Availability Domains 1/2/3 are separate
   physical pools. If the form lets you pick, rotate through them.
3. **Reduce the request** to 1 OCPU / 6 GB to test whether *any* capacity
   exists, then resize up if it succeeds.
4. **Come back later.** Many people succeed on a retry hours later.
5. **Consider a different region** as a last resort — but remember the home
   region is permanent, so do not switch casually.

---

## Step 3 — Open the firewall (console, not the OS)

Networking → **Virtual Cloud Networks** → your VCN → **Security Lists** →
**Add Ingress Rules**. Ingress is `deny` by default in OCI.

| Rule | Source | Protocol | Dest port | Why |
|---|---|---|---|---|
| 0 | `0.0.0.0/0` | TCP | 22 | SSH |
| 1 | `0.0.0.0/0` | TCP | 3000 | the UI |
| 2 | `0.0.0.0/0` | TCP | 8081 | JSON ingest *(optional)* |
| 3 | `0.0.0.0/0` | UDP | 1514 | syslog *(optional)* |
| 4 | `0.0.0.0/0` | TCP | 5151 | syslog TCP *(optional)* |

**Never open 9200 (OpenSearch), 9092 (Redpanda) or 8000 (API).** The prod
overlay already binds those to `127.0.0.1`, so they are unreachable from the
internet even if you add the rules. OpenSearch in particular runs with its
security plugin disabled — anyone who can reach :9200 owns your data.

Narrow the source CIDR on the ingest rules to your own IP once you know it.

---

## Step 4 — Bootstrap the host

```bash
git clone https://github.com/iffatansari/ulpf.git ~/ulpf
cd ~/ulpf
sudo ./deploy/oracle/bootstrap.sh
```

The script is idempotent and does four things:

1. **Installs prerequisites.** OCI's Ubuntu image is *Minimal* — no `curl`,
   `gnupg`, or `ca-certificates`. Without this step the Docker install fails in
   a confusing way.
2. **Sets `vm.max_map_count=262144`.** OpenSearch's bootstrap check refuses to
   start below this. This sysctl is not namespaced, so no container or PaaS can
   set it for you — owning the host is the main reason to run here.
3. **Creates 2 GB of swap.** Not a substitute for the memory limits, but it
   turns an allocation spike into slow degradation instead of the OOM killer
   terminating OpenSearch mid-write.
4. **Installs Docker + Compose v2** from the official apt repository, and
   configures `ufw` as a second firewall layer.

**Log out and back in** afterwards so your user picks up the `docker` group.

---

## Step 5 — Deploy

```bash
cd ~/ulpf
docker compose --env-file deploy/oracle/env.oracle \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  -f docker-compose.oracle.yml \
  up -d --build
```

That builds your own containers and pulls `opensearch:2.11.0` and
`redpanda:v23.3.8`. Both publish `linux/arm64` manifests, so they run natively
on the A1 shape without emulation.

The UI is then at `http://<your-instance-ip>:3000`.

### Memory budget

`docker-compose.oracle.yml` caps every container so the total fits in 12 GB:

| Service | Limit |
|---|---|
| opensearch | 3072 MB (2 GB heap) |
| redpanda | 2560 MB (`--memory=2G`) |
| api | 768 MB |
| orchestrator | 768 MB |
| file_collector | 512 MB |
| http_json_collector | 512 MB |
| syslog_collector | 256 MB |
| ui | 512 MB |
| **total** | **8960 MB** |

Leaving ~3.2 GB for the kernel, Docker, and SSH. Exceeding these is what turns a
busy hour into an OOM kill. Tune them in `deploy/oracle/env.oracle`.

The dev-only `sse_collector` and `live_sim` are behind compose profiles and do
not start. To include them: `--profile sse --profile sim`.

---

## Step 6 — Verify

```bash
docker compose ps                       # all services up
docker compose logs -f opensearch       # wait for "started"
curl -s localhost:3000/api/ping         # UI responds
curl -s localhost:8000/                 # API responds (from inside the host)
docker stats --no-stream                # confirm nothing is near its limit
```

OpenSearch takes 30–60 seconds to become healthy on first boot while it creates
its indices. `api` and `orchestrator` will log connection errors until then and
then recover — that is normal, not a misconfiguration.

---

## Operations

```bash
# deploy a change
git pull && docker compose --env-file deploy/oracle/env.oracle \
  -f docker-compose.yml -f docker-compose.prod.yml \
  -f docker-compose.oracle.yml up -d --build

# logs
docker compose logs -f api

# restart everything after a reboot (containers have restart: unless-stopped,
# but Docker itself does not start until the service is enabled)
sudo systemctl start docker && docker compose -f docker-compose.yml \
  -f docker-compose.prod.yml -f docker-compose.oracle.yml up -d
```

State lives on the **boot volume**, which OCI preserves across reboots and
instance stops — unlike most clouds. A `docker compose down -v` or a rebuild of
the instance will lose it. For anything you care about, attach a separate block
volume and set `ULPF_DATA_DIR` to its mount point in `env.oracle`.

---

## What this gives you that Render cannot

- **Syslog works.** `1514/udp` and `5151/tcp` are real open ports here, so
  `syslog_collector` is in the stack. Render has no UDP at all.
- **Shared uploads work.** `api` and `file_collector` both bind-mount
  `./data/uploads`, so the path-based handoff in `api/routes/uploads.py` is
  correct as written. No code change needed.
- **Redis/DLQ/reprocessing all work**, because it is the real compose file
  rather than a re-architecture.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `max virtual memory areas` in opensearch logs | `vm.max_map_count` too low | run `sysctl -w vm.max_map_count=262144`; the bootstrap script does this |
| `exec format error` on a container | wrong architecture | verify with `docker manifest inspect <image>`; both pinned tags ship `linux/arm64` |
| OOM killed | limits too high for 12 GB | lower them in `env.oracle`, or drop `sse`/`sim` profiles |
| Instance disappears | OCI reclaimed idle Always Free compute | recreate; add a cron ping or use the console regularly |
| Nothing reachable from outside | OCI security list or ufw | check both; they are separate layers |
| 403 from Docker | user not yet in `docker` group | log out and back in |
| `no space left on device` | 47 GB boot volume filled by OpenSearch indices | set index lifecycle management; the indices are the growth |

---

## What is not free

Nothing, as long as you stay inside Always Free. Things that will start
charging if you are not careful:

- Resizing above 2 OCPU / 12 GB
- Boot volumes above 47 GB (the first 200 GB of block storage is free, so you
  have headroom — but the boot volume minimum is 47 GB)
- Egress above 10 TB/month

There is no trial clock on Always Free resources. The $300 credit expires, the
instance does not.
