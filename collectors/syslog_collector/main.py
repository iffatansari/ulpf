import asyncio
import json
import os
import uuid
from typing import Optional

from aiokafka import AIOKafkaProducer

from schema.raw_event import RawEventEnvelope


REDPANDA_BROKER = os.getenv("REDPANDA_BROKER", "redpanda:29092")
RAW_TOPIC = os.getenv("RAW_TOPIC", "logs.raw")
COLLECTOR_ID = os.getenv("COLLECTOR_ID", "syslog-collector-1")
SYSLOG_PORT = int(os.getenv("SYSLOG_PORT", "1514"))


async def send_raw_event(
    producer: AIOKafkaProducer,
    raw_line: str,
    source_id: str,
    source_type: str,
    transport: str,
    format_hint: Optional[str],
):
    print("STEP 1: Creating RawEventEnvelope", flush=True)

    event = RawEventEnvelope(
        event_id=str(uuid.uuid4()),
        source_id=source_id,
        source_type=source_type,
        transport=transport,
        format_hint=format_hint,
        raw_payload=raw_line.strip(),
        collector_id=COLLECTOR_ID,
    )

    print("STEP 2: Sending to Redpanda", flush=True)

    result = await producer.send_and_wait(
        RAW_TOPIC,
        json.dumps(event.model_dump(mode="json")).encode("utf-8"),
    )

    print(f"STEP 3: Sent to {RAW_TOPIC}: {event.event_id} | {result}", flush=True)
async def syslog_server(producer: AIOKafkaProducer):
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", SYSLOG_PORT))
    sock.setblocking(False)

    default_source_id = "syslog-source-1"
    default_source_type = "server"
    default_format_hint = "syslog"

    print(f"Syslog collector listening on UDP {SYSLOG_PORT}")

    while True:
        await asyncio.sleep(0)

        try:
            data, addr = sock.recvfrom(65535)
            line = data.decode("utf-8", errors="replace")

            print(f"RECEIVED: {line} from {addr}", flush=True)


            await send_raw_event(
                producer,
                line,
                source_id=default_source_id,
                source_type=default_source_type,
                transport="syslog",
                format_hint=default_format_hint,
            )

        except BlockingIOError:
            continue

        except Exception as exc:
            print(f"Syslog collector error: {exc}")


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
