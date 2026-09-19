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

    UDP transport does NOT automatically mean the payload is Syslog.
    """

    line = line.strip()

    # CEF logs
    if line.startswith("CEF:"):
        return "cef"

    # RFC-style Syslog, e.g. <34>Sep 19 12:20:00 server1 sshd: login
    if re.match(r"^<\d{1,3}>", line):
        return "syslog"

    # Unknown format
    return "unknown"


async def send_raw_event(
    producer: AIOKafkaProducer,
    raw_line: str,
    source_id: str,
    source_type: str,
    transport: str,
    format_hint: str,
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

    print(f"Detected format: {format_hint}", flush=True)
    print("STEP 2: Sending to Redpanda", flush=True)

    result = await producer.send_and_wait(
        RAW_TOPIC,
        json.dumps(event.model_dump(mode="json")).encode("utf-8"),
    )

    print(
        f"STEP 3: Sent to {RAW_TOPIC}: "
        f"{event.event_id} | {result}",
        flush=True,
    )


class SyslogUDPProtocol(asyncio.DatagramProtocol):
    """
    Event-driven UDP collector.

    datagram_received() is called only when a UDP packet arrives,
    avoiding the continuous polling loop used previously.
    """

    def __init__(self, producer: AIOKafkaProducer):
        self.producer = producer
        self.default_source_id = "syslog-source-1"
        self.default_source_type = "server"

    def datagram_received(self, data: bytes, addr) -> None:
        line = data.decode("utf-8", errors="replace").strip()

        print(
            f"RECEIVED: {line} from {addr}",
            flush=True,
        )

        detected_format = detect_log_format(line)

        print(
            f"Detected format: {detected_format}",
            flush=True,
        )

        asyncio.ensure_future(
            send_raw_event(
                self.producer,
                line,
                source_id=self.default_source_id,
                source_type=self.default_source_type,
                transport="udp",
                format_hint=detected_format,
            )
        )

    def error_received(self, exc: Exception) -> None:
        print(
            f"Syslog collector socket error: {exc}",
            flush=True,
        )


async def main():
    producer = AIOKafkaProducer(
        bootstrap_servers=REDPANDA_BROKER
    )

    await producer.start()

    loop = asyncio.get_running_loop()

    transport, _protocol = await loop.create_datagram_endpoint(
        lambda: SyslogUDPProtocol(producer),
        local_addr=("0.0.0.0", SYSLOG_PORT),
    )

    print(
        f"Syslog collector listening on UDP {SYSLOG_PORT}",
        flush=True,
    )

    try:
        await asyncio.Event().wait()

    finally:
        transport.close()
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())