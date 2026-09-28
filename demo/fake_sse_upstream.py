"""
Fake upstream SSE feed for testing the ULPF SSE collector.

Emits a rotating mix of syslog, CEF and JSON frames over a real chunked
text/event-stream, plus a free-text frame that no parser can handle so the DLQ
has something in it.

Run on the Docker host:
    python demo/fake_sse_upstream.py
    python demo/fake_sse_upstream.py --port 9999 --interval 2
    python demo/fake_sse_upstream.py --crlf --interval 0.5

Then point the collector at it (the container reaches the host this way):
    UPSTREAM_SSE_URL=http://host.docker.internal:9999/events
    SSE_ALLOWED_HOSTS=host.docker.internal
    SSE_ALLOW_INSECURE_HTTP=true
"""

from __future__ import annotations

import argparse
import json
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HOSTS = ["fw-edge-01", "db-primary-01", "api-gateway-02", "win-web-03"]
USERS = ["alice", "bob", "carol", "dave", "erin"]
IPS = ["10.0.0.10", "10.0.0.22", "192.168.5.7", "172.16.3.88"]
ACTIONS = ["login_success", "login_failed", "config_change", "file_delete"]


def syslog_frame(i: int, rng: random.Random) -> str:
    host = rng.choice(HOSTS)
    user = rng.choice(USERS)
    ip = rng.choice(IPS)
    pri = rng.choice([34, 38, 131, 132, 134])
    return (
        f"<{pri}>Sep 16 18:{rng.randint(10, 59)}:{rng.randint(0, 59):02d} "
        f"{host} sshd[{1000 + i}]: Accepted password for {user} from {ip} port {rng.randint(30000, 60000)} ssh2"
    )


def cef_frame(i: int, rng: random.Random) -> str:
    severity = rng.choice([3, 5, 6, 8, 9, 10])
    return (
        f"CEF:0|Vendor|Product|1.0|{100 + i}|UserAction|{severity}|"
        f"src={rng.choice(IPS)} dst=10.0.0.5 suser={rng.choice(USERS)} "
        f"dvchost={rng.choice(HOSTS)} cs1Label=rule_name cs1=allow act=allow"
    )


def json_frame(i: int, rng: random.Random) -> str:
    level = rng.choice([0, 2, 3, 4, 6, "error", "warning", "critical"])
    return json.dumps(
        {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": rng.choice(HOSTS),
            "service": rng.choice(["postgres", "nginx", "iis"]),
            "user": rng.choice(USERS),
            "action": rng.choice(ACTIONS),
            "src_ip": rng.choice(IPS),
            "level": level,
        }
    )


def unknown_frame(i: int, rng: random.Random) -> str:
    return f"gadget exploded while servicing widget {rng.choice(IPS)} user {rng.choice(USERS)}"


def oversized_frame(i: int, rng: random.Random) -> str:
    return "x" * 300_000


class Handler(BaseHTTPRequestHandler):
    interval = 2.0
    crlf = False
    seed = 7
    include_oversized = False

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            body = b'{"status": "ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not self.path.startswith("/events"):
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        rng = random.Random(self.seed)
        counter = 0
        try:
            while True:
                counter += 1
                builders = [syslog_frame, cef_frame, json_frame]
                if counter % 7 == 0:
                    builders.append(unknown_frame)
                if self.include_oversized and counter % 11 == 0:
                    builders.append(oversized_frame)
                build = builders[counter % len(builders)]

                nl = "\r\n" if self.crlf else "\n"
                text = (
                    f": heartbeat {counter}{nl}"
                    f"id: evt-{counter}{nl}"
                    f"data: {build(counter, rng)}{nl}{nl}"
                )
                payload = text.encode("utf-8")
                self.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
                self.wfile.write(payload + b"\r\n")
                self.wfile.flush()
                print(
                    f"[{time.strftime('%H:%M:%S')}] sent {build.__name__} "
                    f"id=evt-{counter} ({len(payload)} bytes)",
                    flush=True,
                )
                time.sleep(self.interval)
        except (BrokenPipeError, ConnectionResetError):
            print("client disconnected", flush=True)

    def log_message(self, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--crlf", action="store_true")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--include-oversized",
        action="store_true",
        help="also emit a 300 KB frame to exercise the size cap",
    )
    args = parser.parse_args()

    Handler.interval = args.interval
    Handler.crlf = args.crlf
    Handler.seed = args.seed
    Handler.include_oversized = args.include_oversized

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"fake SSE upstream on http://{args.host}:{args.port}/events", flush=True)
    print(f"interval={args.interval}s line_endings={'CRLF' if args.crlf else 'LF'}", flush=True)
    print(f"for the container use host.docker.internal:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
