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
SOURCE_ID = os.getenv("SOURCE_ID", os.getenv("ULPF_SOURCE_ID", "syslog-source-1"))
SOURCE_TYPE = os.getenv("SOURCE_TYPE", os.getenv("ULPF_SOURCE_TYPE", "server"))
SYSLOG_PORT = int(os.getenv("SYSLOG_PORT", "1514"))
SYSLOG_TCP_PORT = int(os.getenv("SYSLOG_TCP_PORT", "5151"))
MAX_IN_FLIGHT = max(1, int(os.getenv("SYSLOG_MAX_IN_FLIGHT", "100")))
MAX_TCP_LINE_BYTES = max(1024, int(os.getenv("SYSLOG_MAX_TCP_LINE_BYTES", str(1024 * 1024))))


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

    def __init__(
        self,
        producer: AIOKafkaProducer,
        source_id: str = SOURCE_ID,
        source_type: str = SOURCE_TYPE,
    ):
        self.producer = producer
        self.default_source_id = source_id
        self.default_source_type = source_type
        self.semaphore = asyncio.Semaphore(MAX_IN_FLIGHT)
        self.tasks: set[asyncio.Task[None]] = set()

    async def send(self, line: str, transport: str) -> None:
        async with self.semaphore:
            await send_raw_event(
                self.producer,
                line,
                source_id=self.default_source_id,
                source_type=self.default_source_type,
                transport=transport,
                format_hint=detect_log_format(line),
            )

    def enqueue(self, line: str, transport: str) -> None:
        task = asyncio.create_task(self.send(line, transport))
        self.tasks.add(task)
        task.add_done_callback(self.task_done)

    def task_done(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                print(f"Syslog delivery failed: {exc}", flush=True)

    def datagram_received(self, data: bytes, addr) -> None:
        line = data.decode("utf-8", errors="replace").strip()

        print(
            f"RECEIVED: {line} from {addr}",
            flush=True,
        )

        self.enqueue(line, "udp")

    def error_received(self, exc: Exception) -> None:
        print(
            f"Syslog collector socket error: {exc}",
            flush=True,
        )


async def handle_tcp_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    protocol: SyslogUDPProtocol,
) -> None:
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            if len(line) > MAX_TCP_LINE_BYTES:
                print("Ignoring oversized TCP syslog line", flush=True)
                continue
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                protocol.enqueue(text, "tcp")
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    producer = AIOKafkaProducer(
        bootstrap_servers=REDPANDA_BROKER
    )

    await producer.start()

    loop = asyncio.get_running_loop()

    transport, protocol = await loop.create_datagram_endpoint(
        lambda: SyslogUDPProtocol(producer),
        local_addr=("0.0.0.0", SYSLOG_PORT),
    )
    tcp_server = await asyncio.start_server(
        lambda reader, writer: handle_tcp_client(reader, writer, protocol),
        host="0.0.0.0",
        port=SYSLOG_TCP_PORT,
    )

    print(
        f"Syslog collector listening on UDP {SYSLOG_PORT} and TCP {SYSLOG_TCP_PORT}",
        flush=True,
    )

    try:
        await asyncio.Event().wait()

    finally:
        tcp_server.close()
        await tcp_server.wait_closed()
        transport.close()
        pending = tuple(protocol.tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())