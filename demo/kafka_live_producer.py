"""
Continuous live-stream simulator for the ULPF Kafka pipeline.

Publishes generated security records to the raw topic on a timer, exactly the
way the collectors do, so the orchestrator picks them up and they show up in the
UI as a live stream. Payload formats rotate: syslog, CEF, JSON, and free-form
prose that only the drain3 fallback tier can handle. All four land in Silver,
so the default stream is DLQ-free and every record is normalized.

`malformed_json` is opt-in for when you deliberately want DLQ traffic:
    python demo/kafka_live_producer.py --format syslog,cef,json,malformed_json

The record envelope and the format hint come from the same production code the
collectors use, so anything this produces is accepted by the orchestrator.

Run against the compose broker (Redpanda is exposed on localhost:9092):
    python demo/kafka_live_producer.py
    python demo/kafka_live_producer.py --rate 5 --topic logs.raw
    python demo/kafka_live_producer.py --source-id sse-source-1 --format syslog

Preview without a broker (generates and normalizes, prints, sends nothing):
    python demo/kafka_live_producer.py --dry-run --count 8

Note on the DLQ: the default mix never reaches it, by design. Free text does
not reach it either, as long as drain3 can mine a recognizable identity out of
it (`src=`, `user=`, a bare MAC or IPv4) -- the orchestrator probes
cef/json/syslog, then falls back to drain3-fallback-v1 and stores the result in
Silver at severity low. To land in the DLQ a record needs a known format_hint
whose own parser fails, which is what the opt-in malformed_json generator does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parents[1]
for _path in (_ROOT, _ROOT / "orchestrator", _ROOT / "api"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from aiokafka import AIOKafkaProducer  # noqa: E402

from collectors.sse_collector.main import detect_format_hint  # noqa: E402
from schema.raw_event import RawEventEnvelope  # noqa: E402

VALID_TRANSPORTS = {"sse", "udp", "tcp", "http", "file", "agent"}
VALID_SOURCE_TYPES = {
    "application",
    "cloud",
    "custom",
    "database",
    "edr",
    "firewall",
    "iam",
    "iot",
    "network_device",
    "server",
    "web_server",
}

HOSTS = ["fw-edge-01", "db-primary-01", "api-gateway-02", "win-web-03", "auth-srv-01"]
USERS = ["alice", "bob", "carol", "dave", "erin", "frank"]
IPS = ["10.0.0.10", "10.0.0.22", "192.168.5.7", "172.16.3.88", "10.0.0.99"]
SERVICES = ["postgres", "nginx", "iis", "sshd", "cron"]
ACTIONS = [
    "login_success",
    "login_failed",
    "config_change",
    "file_delete",
    "privilege_escalation",
    "port_scan",
]
PRIS = [34, 38, 131, 132, 133, 134, 135]
CEF_SEVERITIES = [3, 5, 6, 8, 9, 10]
JSON_LEVELS = [0, 2, 3, 4, 6, 7]
JSON_LEVELS_STRINGS = ["error", "warning", "critical", "notice"]


def gen_syslog(rng: random.Random) -> str:
    return (
        f"<{rng.choice(PRIS)}>Sep 16 18:{rng.randint(10, 59)}:{rng.randint(0, 59):02d} "
        f"{rng.choice(HOSTS)} {rng.choice(['sshd', 'sudo', 'CRON'])}[{1000 + rng.randint(1, 9999)}]: "
        f"Accepted password for {rng.choice(USERS)} from {rng.choice(IPS)} "
        f"port {rng.randint(30000, 60000)} ssh2"
    )


def gen_cef(rng: random.Random) -> str:
    return (
        f"CEF:0|Vendor|Product|1.0|{rng.randint(100, 999)}|UserAction|"
        f"{rng.choice(CEF_SEVERITIES)}|src={rng.choice(IPS)} dst=10.0.0.5 "
        f"suser={rng.choice(USERS)} dvchost={rng.choice(HOSTS)} "
        f"cs1Label=rule_name cs1={rng.choice(['allow', 'deny'])} "
        f"act={rng.choice(['allow', 'deny'])}"
    )


def gen_json(rng: random.Random, string_levels: bool = False) -> str:
    levels = JSON_LEVELS_STRINGS if string_levels else JSON_LEVELS
    return json.dumps(
        {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "host": rng.choice(HOSTS),
            "service": rng.choice(SERVICES),
            "user": rng.choice(USERS),
            "action": rng.choice(ACTIONS),
            "src_ip": rng.choice(IPS),
            "level": rng.choice(levels),
        }
    )


def gen_unknown(rng: random.Random) -> str:
    # Free-form prose with no cef/json/syslog structure, so the three primary
    # parsers all decline it. The src=/user= tokens are what drain3 masks, and
    # a masked identity field is exactly what parse_drain requires to return an
    # event instead of None -- so this is a Silver record at severity low, not
    # a DLQ record. Written as bare "user <name>" it has no identity field and
    # does go to the DLQ.
    return (
        f"gadget exploded while servicing widget src={rng.choice(IPS)} "
        f"user={rng.choice(USERS)}"
    )


def gen_malformed_json(rng: random.Random) -> str:
    return '{"host": "%s", "user": "%s", "level": ' % (
        rng.choice(HOSTS),
        rng.choice(USERS),
    )


# Generators whose format_hint is forced to a KNOWN format, so the single
# matching parser is tried and then fails. That is the reliable path into the
# DLQ: a payload no parser can mine an identity from is also rejected, but only
# once drain3 has nothing left. Kept out of DEFAULT_FORMATS so the live stream
# stays DLQ-free unless malformed_json is asked for by name.
FORCED_HINTS = {"malformed_json": "json"}


GENERATORS = {
    "syslog": gen_syslog,
    "cef": gen_cef,
    "json": gen_json,
    "unknown": gen_unknown,
    "malformed_json": gen_malformed_json,
}

# Each of these four parses, so the default stream lands wholly in Silver.
# `unknown` is in the mix on purpose: it is the only generator that exercises
# the drain3-fallback-v1 tier, and it reaches Silver rather than the DLQ
# because gen_unknown emits the src=/user= tokens drain3 masks.
# `malformed_json` stays out -- it is the deliberate-failure opt-in.
DEFAULT_FORMATS = "syslog,cef,json,unknown"


def build_envelope(
    payload: str,
    source_id: str,
    source_type: str,
    transport: str,
    collector_id: str,
    hint: Optional[str] = None,
) -> RawEventEnvelope:
    return RawEventEnvelope(
        event_id=str(uuid.uuid4()),
        source_id=source_id,
        source_type=source_type,
        transport=transport,
        format_hint=hint or detect_format_hint(payload),
        raw_payload=payload,
        collector_id=collector_id,
    )


def describe(envelope: RawEventEnvelope) -> str:
    try:
        from orchestrator.main import normalize_raw_event
    except ImportError:
        return f"hint={envelope.format_hint}"

    normalized, attempted = normalize_raw_event(envelope)
    if normalized is None:
        return f"hint={envelope.format_hint:8} -> DLQ (tried {len(attempted)} parsers)"
    return (
        f"hint={envelope.format_hint:8} -> {normalized.severity:8} "
        f"{normalized.parser_id:18} user={normalized.user} dev={normalized.device_id}"
    )


class FakeProducer:
    """Stand-in used by --dry-run so the generator can be checked without Kafka."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def start(self) -> None:
        return

    async def stop(self) -> None:
        return

    async def send_and_wait(self, topic: str, value: bytes, key: Any = None) -> None:
        self.sent.append(value)


async def run(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    for name in formats:
        if name not in GENERATORS:
            print(f"unknown format: {name}", flush=True)
            print(f"choose from: {', '.join(GENERATORS)}", flush=True)
            return 2

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    producer: Any
    if args.dry_run:
        producer = FakeProducer()
    else:
        producer = AIOKafkaProducer(
            bootstrap_servers=args.bootstrap,
            acks="all",
            enable_idempotence=True,
        )
        await producer.start()

    topic = args.topic
    target = f"dry-run (no broker)" if args.dry_run else f"{args.bootstrap} topic={topic}"
    print(
        f"producing to {target} rate={args.rate}/s formats={','.join(formats)} "
        f"source_id={args.source_id} transport={args.transport}",
        flush=True,
    )
    if not args.dry_run:
        print(f"open the UI: http://localhost:3000/events/normalized", flush=True)

    sent = 0
    dlq = 0
    started = time.monotonic()
    interval = 1.0 / args.rate if args.rate > 0 else 0.0
    next_send = started

    try:
        while not stop.is_set():
            name = formats[sent % len(formats)]
            if name == "json":
                payload = gen_json(rng, args.string_levels)
            else:
                payload = GENERATORS[name](rng)
            envelope = build_envelope(
                payload,
                source_id=args.source_id,
                source_type=args.source_type,
                transport=args.transport,
                collector_id=args.collector_id,
                hint=FORCED_HINTS.get(name),
            )
            wire = json.dumps(
                envelope.model_dump(mode="json"), separators=(",", ":")
            ).encode("utf-8")

            await producer.send_and_wait(topic, wire, key=args.source_id.encode())
            sent += 1
            if name in FORCED_HINTS:
                dlq += 1
            if sent <= args.preview:
                print(f"  {sent:4d} {name:14} {describe(envelope)}", flush=True)

            if args.count and sent >= args.count:
                break
            next_send += interval
            sleep_for = next_send - time.monotonic()
            if sleep_for > 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=sleep_for)
                except asyncio.TimeoutError:
                    pass
            else:
                next_send = time.monotonic()
    except Exception as exc:
        print(f"stopped: {type(exc).__name__}: {exc}", flush=True)
        return 1
    finally:
        if not args.dry_run:
            await producer.stop()

    elapsed = max(time.monotonic() - started, 0.001)
    print(
        f"sent {sent} records in {elapsed:.1f}s ({sent / elapsed:.1f}/s), "
        f"~{dlq} expected in the DLQ",
        flush=True,
    )
    return 0


def env_str(name: str, fallback: str) -> str:
    value = os.getenv(name, "").strip()
    return value or fallback


def env_float(name: str, fallback: float) -> float:
    try:
        return float(os.getenv(name, "").strip())
    except ValueError:
        return fallback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bootstrap",
        default=env_str("REDPANDA_BROKER", "localhost:9092"),
        help="defaults to REDPANDA_BROKER, else localhost:9092",
    )
    parser.add_argument("--topic", default=env_str("RAW_TOPIC", "logs.raw"))
    parser.add_argument(
        "--rate", type=float, default=env_float("SIM_RATE", 2.0), help="records per second"
    )
    parser.add_argument("--count", type=int, default=0, help="stop after N (0 = forever)")
    parser.add_argument(
        "--format", default=env_str("SIM_FORMAT", DEFAULT_FORMATS)
    )
    parser.add_argument("--source-id", default=env_str("SIM_SOURCE_ID", "sse-source-1"))
    parser.add_argument(
        "--source-type", default=env_str("SIM_SOURCE_TYPE", "application")
    )
    parser.add_argument("--transport", default=env_str("SIM_TRANSPORT", "sse"))
    parser.add_argument(
        "--collector-id", default=env_str("SIM_COLLECTOR_ID", "kafka-live-producer")
    )
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument(
        "--string-levels",
        action="store_true",
        help=(
            "emit JSON level as a string instead of a number. Silver maps "
            "extensions.original_json.level from the first document it sees, so "
            "mixing both types makes OpenSearch reject the whole document."
        ),
    )
    parser.add_argument("--preview", type=int, default=10, help="lines to print")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="generate and normalize without connecting to Kafka",
    )
    args = parser.parse_args()

    if args.transport not in VALID_TRANSPORTS:
        print(f"--transport must be one of {sorted(VALID_TRANSPORTS)}", flush=True)
        return 2
    if args.source_type not in VALID_SOURCE_TYPES:
        print(f"--source-type must be one of {sorted(VALID_SOURCE_TYPES)}", flush=True)
        return 2

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
