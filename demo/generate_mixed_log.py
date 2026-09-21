"""
Generate mixed_security_logs.log — the Module 2 demo asset.

Creates a realistic security log file mixing four formats so the
pipeline is visibly exercised end to end:

  syslog (RFC3162 style)  ~50%
  JSON   (CEF-like JSON)  ~25%
  CEF    ~15%
  unknown (free text)     ~10%

Run:  python demo/generate_mixed_log.py [line_count=4000]
"""

import json
import random
import sys
from pathlib import Path

OUT = Path(__file__).parent / "mixed_security_logs.log"

USERS = ["alice", "bob", "carol", "dave", "erin", "frank"]
ACTIONS = ["USER_LOGIN", "USER_LOGOUT", "CONFIG_CHANGE", "FILE_DELETE"]
IPS = ["10.0.0.10", "10.0.0.22", "192.168.5.7", "172.16.3.88", "10.0.0.99"]


def main(count: int) -> None:
    rng = random.Random(2026)
    lines = []

    for i in range(count):
        user = rng.choice(USERS)
        ip = rng.choice(IPS)
        action = rng.choice(ACTIONS)
        roll = rng.random()

        if roll < 0.50:
            sev = rng.choice([6, 5, 4, 3])
            lines.append(
                f"<{sev}>Sep 21 14:{rng.randint(0,59):02d}:{rng.randint(0,59):02d} "
                f"fw-01 router: {action} from {ip} user={user}"
            )
        elif roll < 0.75:
            lines.append(
                json.dumps(
                    {
                        "timestamp": f"2026-09-21T14:{rng.randint(0,59):02d}:00Z",
                        "event": action,
                        "src": ip,
                        "user": user,
                        "severity": rng.choice(["low", "medium", "high"]),
                        "device": "app-server-1",
                    }
                )
            )
        elif roll < 0.90:
            severity_id = rng.choice([1, 3, 5, 7])
            lines.append(
                f"CEF:0|VendorX|AppY|1.0|{rng.randint(100,999)}|{action}"
                f"|{severity_id}|src={ip} suser={user} device=/var/log/app"
            )
        else:
            lines.append(
                f"[{rng.randint(0,23):02d}:{rng.randint(0,59):02d}] ignoring "
                f"unstructured noise from {ip}"
            )

    # A few blank lines are deliberate — they are counted, not errors.
    for pos in (150, 900, 2100, 3300):
        if pos < len(lines):
            lines[pos] = ""

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} lines -> {OUT}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 4000)