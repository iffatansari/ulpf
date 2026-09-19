import asyncio
import json
import os
import re
import uuid

from aiokafka import AIOKafkaProducer

from schema.raw_event import RawEventEnvelope


REDPANDA_BROKER = os.getenv("REDPANDA_BROKER", "redpanda:29092")
RAW_TOPIC = os.getenv("RAW_TOPIC", "logs.raw")
COLLECTOR_ID = os.getenv("COLLECTOR_ID", "syslog-collector-1")
SYSLOG_PORT = int(os.getenv("SYSLOG_PORT", "1514"))


def detect_log_format(line: str) -> str:
    """
    Detect the format of a UDP log payload.

    Important:
    UDP transport does NOT automatically mean the payload is Syslog.
    We only label the payload when we have a recognizable signature.
    """

    line = line.strip()

    # CEF logs have a very strong signature.
    if line.startswith("CEF:"):
        return "cef"

    # RFC-style Syslog normally begins with a PRI value such as <34>.
    # Example:
    # <34>Sep 19 12:20:00 server1 sshd: Accepted login
    if re.match(r"^<\d{1,3}>", line):
        return "syslog"

    # We don't know the format yet.
    return "unknown"


async def send_raw_event(
    producer,
    raw_line,
    source_id,
    source_type,
    transport,
    format_hint,
):
    print(
        "STEP 1: Creating RawEventEnvelope",
        flush=True,
    )

    event = RawEventEnvelope(
        event_id=str(uuid.uuid4()),
        source_id=source_id,
        source_type=source_type,
        transport=transport,
        format_hint=format_hint,
        raw_payload=raw_line.strip(),
        collector_id=COLLECTOR_ID,
    )

    print(
        f"Detected format: {format_hint}",
        flush=True,
    )

    print(
        "STEP 2: Sending to Redpanda",
        flush=True,
    )

    result = await producer.send_and_wait(
        RAW_TOPIC,
        json.dumps(
            event.model_dump(mode="json")
        ).encode("utf-8"),
    )

    print(
        f"STEP 3: Sent to {RAW_TOPIC}: "
        f"{event.event_id} | {result}",
        flush=True,
    )


async def syslog_server(producer):
    import socket

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM,
    )

    sock.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_REUSEADDR,
        1,
    )

    sock.bind(
        ("0.0.0.0", SYSLOG_PORT)
    )

    sock.setblocking(False)

    default_source_id = "syslog-source-1"
    default_source_type = "server"

    print(
        f"Syslog collector listening on UDP {SYSLOG_PORT}",
        flush=True,
    )

    while True:
        await asyncio.sleep(0)

        try:
            data, addr = sock.recvfrom(65535)

            line = data.decode(
                "utf-8",
                errors="replace",
            ).strip()

            print(
                f"RECEIVED: {line} from {addr}",
                flush=True,
            )

            detected_format = detect_log_format(line)

            await send_raw_event(
                producer,
                line,
                source_id=default_source_id,
                source_type=default_source_type,
                transport="udp",
                format_hint=detected_format,
            )

        except BlockingIOError:
            continue

        except Exception as exc:
            print(
                f"Syslog collector error: {exc}",
                flush=True,
            )


async def main():
    producer = AIOKafkaProducer(
        bootstrap_servers=REDPANDA_BROKER
    )

    await producer.start()

    try:
        await syslog_server(producer)

    finally:
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())