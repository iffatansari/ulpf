"""
Live SSE -> Bronze -> normalization lab (no Docker required).

Runs the real pipeline in one process against a real local HTTP server that
speaks chunked text/event-stream:

    local SSE upstream  ->  collectors.sse_collector.stream_sse / publish_sse_message
                        ->  (Kafka bytes, captured by a fake producer)
                        ->  orchestrator.normalize_raw_event
                        ->  NormalizedEvent or DLQ record

Everything except Redpanda and OpenSearch is production code.

Numeric severity uses the syslog scale (0 emerg .. 7 debug), so JSON "level": 3
normalizes to high and a syslog <34> (crit) normalizes to critical.

Run:
    python demo/stream_normalization_lab.py
    python demo/stream_normalization_lab.py --crlf
    python demo/stream_normalization_lab.py --max-event-bytes 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator, Optional

_ROOT = Path(__file__).resolve().parents[1]
# Mirror the container layout used by tests/conftest.py: PYTHONPATH=/app with
# WORKDIR=/app/orchestrator, so orchestrator's `parsers.*` imports resolve.
for _path in (_ROOT, _ROOT / "orchestrator", _ROOT / "api"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import httpx  # noqa: E402

from collectors.sse_collector import main as sse  # noqa: E402
from orchestrator.main import create_dlq_record, normalize_raw_event  # noqa: E402
from schema.raw_event import RawEventEnvelope  # noqa: E402

SYSLOG = "<34>Sep 16 18:40:04 fw-edge-01 sshd[2211]: Accepted password for alice from 10.0.0.10 port 51244 ssh2"
SYSLOG_PRI_ONLY = "<34>Sep 16 18:41:00 fw-edge-01 CRON: session opened for bob"
CEF = (
    "CEF:0|Vendor|Product|1.0|100|Success|2|rt=1789478405000 src=10.0.0.22 dst=10.0.0.5 "
    "spt=4432 dpt=443 suser=erin dvchost=db-primary-01 cs1Label=rule_name cs1=allow "
    "cs2Label=src_zone cs2=dmz act=allow"
)
JSON_LOG = json.dumps(
    {
        "timestamp": "2026-09-16T18:40:07Z",
        "host": "db-primary-01",
        "service": "postgres",
        "user": "carol",
        "action": "login_failed",
        "src_ip": "192.168.5.7",
        "severity": "error",
    }
)
JSON_NUMERIC_SEVERITY = json.dumps(
    {
        "timestamp": "2026-09-16T18:40:08Z",
        "host": "win-web-03",
        "service": "iis",
        "user": "erin",
        "action": "access_denied",
        "src_ip": "10.0.0.31",
        "level": 3,
    }
)
MULTILINE_JSON = (
    '{\n  "timestamp": "2026-09-16T18:40:09Z",\n  "host": "api-gateway-02",\n'
    '  "service": "nginx",\n  "user": "dave",\n  "action": "config_change"\n}'
)
UNKNOWN = "gadget exploded while servicing widget 10.0.0.99 user frank"


def script(crlf: bool) -> list[tuple[float, str]]:
    nl = "\r\n" if crlf else "\n"
    frames: list[tuple[float, str]] = [
        (0.0, f": upstream stream open{nl}{nl}"),
        (0.0, f"retry: 250{nl}{nl}"),
        (0.15, f"id: syslog-1{nl}data: {SYSLOG}{nl}{nl}"),
        (0.15, f"id: syslog-pri{nl}data: {SYSLOG_PRI_ONLY}{nl}{nl}"),
        (0.15, f"id: cef-1\nevent: cef_alert\ndata: {CEF}\n\n".replace("\n", nl)),
        (0.15, f"id: json-1{nl}data: {JSON_LOG}{nl}{nl}"),
        (0.15, f"id: json-num-1{nl}data: {JSON_NUMERIC_SEVERITY}{nl}{nl}"),
        (
            0.15,
            "id: multi-1" + nl
            + "data: {" + nl
            + 'data:   "timestamp": "2026-09-16T18:40:09Z",' + nl
            + 'data:   "host": "api-gateway-02",' + nl
            + 'data:   "service": "nginx",' + nl
            + 'data:   "user": "dave",' + nl
            + 'data:   "action": "config_change"' + nl
            + "data: }" + nl + nl,
        ),
        (0.15, f"id: unknown-1{nl}data: {UNKNOWN}{nl}{nl}"),
        (0.15, f"data: [DONE]{nl}{nl}"),
    ]
    return frames


class UpstreamHandler(BaseHTTPRequestHandler):
    frames: list[tuple[float, str]] = []
    seen_headers: list[dict[str, str]] = []

    def do_GET(self) -> None:
        UpstreamHandler.seen_headers.append(dict(self.headers))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for delay, text in UpstreamHandler.frames:
            if delay:
                time.sleep(delay)
            payload = text.encode("utf-8")
            self.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
            self.wfile.write(payload + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def log_message(self, *args: Any) -> None:
        return


class CapturingProducer:
    """Stands in for AIOKafkaProducer and keeps the exact wire bytes."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_and_wait(self, topic: str, value: bytes) -> None:
        self.sent.append({"topic": topic, "value": value})


def summarize(normalized: Any) -> str:
    return (
        f"parser={normalized.parser_id} tier={normalized.parser_tier} "
        f"conf={normalized.confidence_score:.2f} sev={normalized.severity} "
        f"time={normalized.time.isoformat()} user={normalized.user} "
        f"device={normalized.device_id} app={normalized.app_id} "
        f"src={normalized.src_endpoint} action={normalized.action}"
    )


async def run(url: str, config: sse.SSECollectorConfig) -> int:
    producer = CapturingProducer()
    stop_event = asyncio.Event()
    frames_seen = 0
    ids_sent: list[str] = []

    async def watching_stream(**kwargs: Any):
        nonlocal frames_seen
        async for message in sse.stream_sse(**kwargs):
            frames_seen += 1
            if message.retry is not None:
                print(f"  [frame {frames_seen}] control: retry={message.retry}ms id={message.id}")
            elif message.data is None:
                print(f"  [frame {frames_seen}] control: id-only id={message.id}")
            else:
                head = message.data.splitlines()[0][:58]
                print(f"  [frame {frames_seen}] data id={message.id} payload={head!r}")
            yield message

    async def drive() -> None:
        nonlocal ids_sent
        task = asyncio.create_task(
            sse.consume_forever(
                producer=producer,
                client=httpx.AsyncClient(timeout=None),
                config=config,
                stop_event=stop_event,
                stream_factory=watching_stream,
            )
        )
        await asyncio.sleep(2.0)
        stop_event.set()
        try:
            await asyncio.wait_for(task, timeout=10)
        except asyncio.TimeoutError:
            task.cancel()

    await drive()

    print(f"\nframes parsed: {frames_seen}   envelopes published to Kafka: {len(producer.sent)}")
    print("\n--- Bronze (logs.raw) -> Silver (silver-events) ---")
    dlq = 0
    for record in producer.sent:
        envelope = RawEventEnvelope.model_validate(json.loads(record["value"]))
        if envelope.transport_metadata:
            ids_sent.append(envelope.transport_metadata.get("sse_event_id", ""))
        normalized, attempted = normalize_raw_event(envelope)
        label = envelope.transport_metadata.get("sse_event_id") or envelope.event_id[:8]
        if normalized is None:
            dlq_record = create_dlq_record(envelope, attempted)
            dlq += 1
            print(f"[{label}] DLQ classification={dlq_record.classification} status={dlq_record.status}")
            print(f"        parsers attempted: {attempted}")
            continue
        print(f"[{label}] {summarize(normalized)}")

    print(f"\nnormalized: {len(producer.sent) - dlq}   dlq: {dlq}")
    print(f"upstream ids carried into transport_metadata: {ids_sent}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crlf", action="store_true", help="emit CRLF line endings upstream")
    parser.add_argument("--max-event-bytes", type=int, default=4096)
    parser.add_argument("--max-wire-bytes", type=int, default=900_000)
    args = parser.parse_args()

    UpstreamHandler.frames = script(args.crlf)
    server = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    host, port = server.server_address[0], server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://{host}:{port}/events"
    config = sse.SSECollectorConfig(
        upstream_url=url,
        source_id="sse-source-lab",
        source_type="application",
        collector_id="sse-collector-lab",
        raw_topic="logs.raw",
        bearer_token="",
        allowed_hosts=(host,),
        connect_timeout_seconds=5.0,
        read_timeout_seconds=5.0,
        reconnect_initial_seconds=0.1,
        reconnect_max_seconds=1.0,
        max_event_bytes=args.max_event_bytes,
        max_wire_bytes=args.max_wire_bytes,
        format_hint="auto",
        allow_insecure_http=True,
        retry_max_seconds=5.0,
    )

    print(f"upstream: {url}  line_endings={'CRLF' if args.crlf else 'LF'}")
    print(f"max_event_bytes={args.max_event_bytes}  max_wire_bytes={args.max_wire_bytes}\n")
    try:
        return asyncio.run(run(url, config))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
