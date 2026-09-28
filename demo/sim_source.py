"""
Point the simulated live stream at a real source, automatically.

The `SIM_SOURCE_ID` in .env must equal the source_id the API assigned in the
UI. Create the source by hand and the two drift apart: the collector keeps
producing, but every event is attributed to an id that is not in the registry,
so the source page shows zeros with no obvious reason.

This finds the source by name (creating it if needed), writes the id into .env
and reports what to do next.

    python demo/sim_source.py --name "Simulated SOC feed"
    python demo/sim_source.py --name "Simulated SOC feed" --start
    python demo/sim_source.py --list

--start runs `docker compose --profile sim up -d --build --force-recreate
live_sim`, so the whole "source exists and is streaming" state is one command.
It also resets the pipeline counters first, so a run always starts from zero
instead of appending to whatever the previous run left behind. Pass --no-reset
if you actually want to keep the old totals.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"

DEFAULT_API = os.getenv("ULPF_API", "http://localhost:8000")
DEFAULT_OS = os.getenv("ULPF_OPENSEARCH", "http://localhost:9200")
DEFAULT_NAME = "Simulated SOC feed"

# The orchestrator owns these three and recreates them on startup; the API
# owns ulpf-sources/ulpf-uploads and recreates those itself.
PIPELINE_INDICES = ("ulpf-bronze", "ulpf-silver", "ulpf-dlq")


def api(path: str, method: str = "GET", body: dict | None = None):
    request = urllib.request.Request(
        f"{DEFAULT_API}{path}",
        method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"API {method} {path} failed ({exc.code}): {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"cannot reach the ULPF API at {DEFAULT_API} ({exc.reason}).\n"
            "Start the stack first:  docker compose up -d"
        )


def list_sources() -> list[dict]:
    return api("/sources").get("sources", [])


def find_by_name(name: str, sources: list[dict]) -> dict | None:
    target = name.strip().lower()
    for source in sources:
        if source.get("name", "").strip().lower() == target:
            return source
    return None


def find_by_env_id(sources: list[dict]) -> dict | None:
    """Resolve the SIM_SOURCE_ID already recorded in .env, if it still exists.

    Without this, a name mismatch silently creates a second source instead of
    reusing the one the feed is already pointed at.
    """
    if not ENV_FILE.exists():
        return None
    known = {source["source_id"]: source for source in sources}
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if raw.strip().startswith("SIM_SOURCE_ID="):
            source_id = raw.split("=", 1)[1].strip()
            if source_id and source_id in known:
                return known[source_id]
    return None


def create_source(name: str) -> dict:
    return api(
        "/sources",
        method="POST",
        body={
            "name": name,
            "source_type": "application",
            "transport": "kafka_sim",
            "expected_format": "mixed",
            "description": "simulated live stream via demo/kafka_live_producer.py",
        },
    )["source"]


def write_env(source_id: str) -> bool:
    line = f"SIM_SOURCE_ID={source_id}"
    existing = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    replaced = False
    out: list[str] = []
    for raw in existing:
        if raw.strip().startswith("SIM_SOURCE_ID="):
            out.append(line)
            replaced = True
        else:
            out.append(raw)
    if not replaced:
        out.extend(["", line])
    ENV_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    return replaced


def stats(source_id: str) -> dict | None:
    try:
        return api(f"/sources/{source_id}/stats")
    except SystemExit:
        return None


def api_ok() -> bool:
    try:
        with urllib.request.urlopen(f"{DEFAULT_API}/dashboard", timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def ensure_stack() -> bool:
    """Bring the core stack up if it is not already running.

    Without this the script only works on an already-running stack, which
    forces the "docker compose up -d, then python ..." two-step ritual.
    """
    if api_ok():
        return True
    print("stack is down, starting it ...", flush=True)
    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"docker compose up failed ({result.returncode})")
    for attempt in range(30):
        time.sleep(2)
        if api_ok():
            print("  api is up")
            return True
        if attempt == 0:
            print("  waiting for the api ...", flush=True)
    raise SystemExit(
        f"the api never became reachable at {DEFAULT_API}.\n"
        "Check:  docker compose ps   and   docker compose logs api"
    )


def reset_indices() -> None:
    """Wipe bronze/silver/dlq so a fresh run really starts from zero.

    The counts in the dashboard are cumulative totals from OpenSearch, so
    restarting live_sim alone keeps appending to the previous run's numbers.
    Deleting the indices is not enough on its own: the orchestrator must be
    restarted afterwards to recreate them, or every event is rejected with
    index_rejected and lands in the DLQ.
    """
    print("resetting pipeline ...", flush=True)
    subprocess.run(
        ["docker", "compose", "stop", "live_sim"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    for index in PIPELINE_INDICES:
        request = urllib.request.Request(f"{DEFAULT_OS}/{index}", method="DELETE")
        try:
            with urlopen(request) as response:
                status = response.status
            label = "deleted" if status == 200 else f"status {status}"
        except urllib.error.HTTPError as exc:
            # 404 just means the index was not there to begin with.
            label = "absent" if exc.code == 404 else f"failed ({exc.code})"
        print(f"  {index}: {label}")
    for service in ("orchestrator", "api"):
        print(f"  restarting {service} ...", flush=True)
        subprocess.run(
            ["docker", "compose", "restart", service],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    time.sleep(2)


def start_sim() -> None:
    print("starting live_sim ...", flush=True)
    result = subprocess.run(
        [
            "docker", "compose", "--profile", "sim",
            # --build is load-bearing: --force-recreate reuses the existing
            # image, so edits to the generators would otherwise never reach
            # the container and a stale mix would keep running.
            "up", "-d", "--build", "--force-recreate", "live_sim",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout or "")
        raise SystemExit(f"docker compose failed ({result.returncode})")
    for line in (result.stdout or "").splitlines():
        if "Started" in line or "Recreated" in line:
            print(f"  {line.strip()}")


def main() -> int:
    global DEFAULT_API
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--start", action="store_true", help="also (re)start live_sim")
    parser.add_argument(
        "--no-reset",
        dest="reset",
        action="store_false",
        help="keep the previous run's counters instead of starting from zero",
    )
    parser.set_defaults(reset=True)
    parser.add_argument("--list", action="store_true", help="list sources and exit")
    args = parser.parse_args()
    DEFAULT_API = args.api

    if args.start:
        ensure_stack()

    if args.list:
        sources = list_sources()
        if not sources:
            print("no sources registered")
        for source in sources:
            print(f"  {source['source_id']}  {source['name']}  [{source['transport']}]")
        return 0

    sources = list_sources()
    source = find_by_name(args.name, sources)
    if source is None:
        source = find_by_env_id(sources)
        if source is not None:
            print(f"no source named {args.name!r}, reusing .env id -> {source['source_id']}")
    if source is None:
        print(f"creating source {args.name!r} ...", flush=True)
        source = create_source(args.name)
        print(f"  created {source['source_id']}")
    else:
        print(f"found source {source['name']!r} -> {source['source_id']}")

    source_id = source["source_id"]
    replaced = write_env(source_id)
    print(f"  .env SIM_SOURCE_ID={source_id}" + ("" if replaced else "  (added)"))

    if args.start:
        if args.reset:
            reset_indices()
        start_sim()
        print("waiting for the pipeline ...", flush=True)
        for _ in range(12):
            time.sleep(5)
            snapshot = stats(source_id)
            if snapshot and snapshot.get("raw_events"):
                print(
                    f"  raw={snapshot['raw_events']} "
                    f"normalized={snapshot['normalized_events']} "
                    f"dlq={snapshot['dlq_events']} "
                    f"success={snapshot['success_rate']}%"
                )
                print(f"\nopen http://localhost:3000/sources/{source_id}")
                return 0
        print("  no events yet - check: docker compose logs live_sim")

    print(f"\nnext: python demo/sim_source.py --start")
    print(f"then:  http://localhost:3000/sources/{source_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
