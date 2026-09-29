import asyncio
import json
import math
import os
import re
import signal
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Optional
from urllib.parse import urlsplit

import httpx
from aiokafka import AIOKafkaProducer
from aiokafka.errors import (
    KafkaConfigurationError,
    KafkaError,
    MessageSizeTooLargeError,
    ProducerClosed,
    UnsupportedVersionError,
)

from schema.raw_event import RawEventEnvelope


MIN_RETRY_DELAY_SECONDS = 0.1
MAX_EVENT_BYTES_LIMIT = 1_000_000
MAX_WIRE_BYTES_LIMIT = 900_000

VALID_SOURCE_TYPES = {
    "network_device",
    "server",
    "application",
    "database",
    "cloud",
    "iot",
    "custom",
}
VALID_FORMAT_HINTS = {"auto", "json", "cef", "syslog", "unknown"}
BOM_UTF8 = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class SSEMessage:
    data: Optional[str]
    event: str = "message"
    id: Optional[str] = None
    retry: Optional[int] = None
    id_in_frame: bool = True


class SSEParser:
    def __init__(self, max_event_bytes: int, last_event_id: str = ""):
        if max_event_bytes < 1:
            raise ValueError("max_event_bytes must be positive")
        self.max_event_bytes = max_event_bytes
        self._line = bytearray()
        self._line_capacity = max_event_bytes + 1
        self._pending_cr = False
        self._skip_event = False
        self._frame_bytes = 0
        self._data_lines: list[str] = []
        self._has_data = False
        self._has_fields = False
        self._event_type = "message"
        self._last_event_id: Optional[str] = last_event_id or None
        self._id_in_frame = False
        self._retry: Optional[int] = None
        self._bom_prefix = bytearray()
        self._bom_checked = False
        self._closed = False
        self.dropped_frames = 0
        self._drop_reported = False

    def feed(self, chunk: bytes) -> list[SSEMessage]:
        if self._closed:
            raise RuntimeError("SSE parser is closed")
        if not chunk:
            return []

        data = self._remove_initial_bom(chunk)
        if not data:
            return []

        messages: list[SSEMessage] = []
        offset = 0
        line_cursor = data.find(b"\n")
        cr_cursor = data.find(b"\r")
        while offset < len(data):
            if self._pending_cr:
                self._pending_cr = False
                if data[offset] == 0x0A:
                    offset += 1
                    line_cursor = data.find(b"\n", offset)
                    continue

            line_end = min(
                line_cursor if line_cursor >= 0 else len(data),
                cr_cursor if cr_cursor >= 0 else len(data),
            )

            self._append_segment(data[offset:line_end])
            if line_end >= len(data):
                break

            if data[line_end] == 0x0D:
                self._pending_cr = True
                cr_cursor = data.find(b"\r", line_end + 1)
            else:
                line_cursor = data.find(b"\n", line_end + 1)
            offset = line_end + 1
            line = bytes(self._line)
            self._line.clear()
            message = self._process_line(line)
            if message is not None:
                messages.append(message)

        return messages

    def close(self) -> list[SSEMessage]:
        if self._closed:
            return []
        self._closed = True

        if not self._bom_checked and self._bom_prefix:
            prefix = bytes(self._bom_prefix)
            self._bom_prefix.clear()
            if not prefix.startswith(BOM_UTF8):
                self._line.extend(prefix[: self._line_capacity])

        self._line.clear()
        self._reset_event_fields()
        self._frame_bytes = 0
        return []

    def _append_segment(self, segment: bytes) -> None:
        available = self._line_capacity - len(self._line)
        if available > 0 and segment:
            self._line.extend(segment[:available])

    def _remove_initial_bom(self, chunk: bytes) -> bytes:
        if self._bom_checked:
            return chunk
        combined = bytes(self._bom_prefix) + chunk
        if len(combined) < len(BOM_UTF8) and BOM_UTF8.startswith(combined):
            self._bom_prefix = bytearray(combined)
            return b""
        self._bom_checked = True
        self._bom_prefix.clear()
        if combined.startswith(BOM_UTF8):
            return combined[len(BOM_UTF8) :]
        return combined

    @staticmethod
    def _is_safe_field_value(value: str, max_length: int) -> bool:
        return (
            value.isascii()
            and len(value) <= max_length
            and all(32 <= ord(character) < 127 for character in value)
        )

    def _mark_dropped(self) -> None:
        self.dropped_frames += 1
        if not self._drop_reported:
            self._drop_reported = True
            print(
                f"dropped SSE frame exceeding {self.max_event_bytes} bytes",
                flush=True,
            )

    def _process_line(self, raw_line: bytes) -> Optional[SSEMessage]:
        if self._skip_event:
            if not raw_line:
                self._skip_event = False
                self._frame_bytes = 0
            return None

        if raw_line.startswith(b":"):
            return None

        if len(raw_line) > self.max_event_bytes:
            if raw_line.split(b":", 1)[0] == b"data":
                self._mark_dropped()
                self._skip_event = True
                self._reset_event_fields()
                self._frame_bytes = 0
            return None

        self._frame_bytes += len(raw_line) + 1
        if self._frame_bytes > self.max_event_bytes:
            self._mark_dropped()
            self._skip_event = True
            self._reset_event_fields()
            if not raw_line:
                self._skip_event = False
                self._frame_bytes = 0
            return None

        if not raw_line:
            return self._dispatch()

        line = raw_line.decode("utf-8", errors="replace")
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]

        if field == "data":
            self._data_lines.append(value)
            self._has_data = True
            self._has_fields = True
        elif field == "event":
            if not value:
                self._event_type = "message"
                self._has_fields = True
            elif self._is_safe_field_value(value, 256):
                self._event_type = value
                self._has_fields = True
        elif field == "id":
            if self._is_safe_field_value(value, 512):
                self._last_event_id = value
                self._id_in_frame = True
                self._has_fields = True
        elif (
            field == "retry"
            and value.isascii()
            and value.isdigit()
            and len(value) <= 19
        ):
            self._retry = min(int(value), 2_147_483_647)
            self._has_fields = True

        return None

    def _dispatch(self) -> Optional[SSEMessage]:
        if self._skip_event:
            self._skip_event = False
            self._reset_event_fields()
            self._frame_bytes = 0
            return None
        if not self._has_fields:
            self._frame_bytes = 0
            return None

        data = "\n".join(self._data_lines) if self._has_data else None
        message = SSEMessage(
            data=data,
            event=self._event_type,
            id=self._last_event_id,
            retry=self._retry,
            id_in_frame=self._id_in_frame,
        )
        self._reset_event_fields()
        self._frame_bytes = 0
        return message

    def _reset_event_fields(self) -> None:
        self._data_lines.clear()
        self._has_data = False
        self._has_fields = False
        self._event_type = "message"
        self._retry = None
        self._id_in_frame = False


def detect_format_hint(payload: str, override: str = "auto") -> str:
    if override != "auto":
        return override

    stripped = payload.strip()
    if stripped.startswith("CEF:"):
        return "cef"
    if re.match(r"^<\d{1,3}>", stripped):
        return "syslog"
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
            return "json"
        except (json.JSONDecodeError, RecursionError, ValueError):
            return "unknown"
    return "unknown"


@dataclass
class PublishStats:
    wire_skips: int = 0
    broker_skips: int = 0


async def publish_sse_message(
    producer: AIOKafkaProducer,
    message: SSEMessage,
    source_id: str,
    source_type: str,
    collector_id: str,
    raw_topic: str,
    format_hint: str = "auto",
    max_wire_bytes: Optional[int] = None,
    stats: Optional[PublishStats] = None,
) -> Optional[RawEventEnvelope]:
    if message.data is None:
        return None

    transport_metadata: dict[str, str] = {}
    if message.id is not None and message.id_in_frame:
        transport_metadata["sse_event_id"] = message.id
    if message.event and message.event != "message":
        transport_metadata["sse_event_type"] = message.event

    event = RawEventEnvelope(
        event_id=str(uuid.uuid4()),
        source_id=source_id,
        source_type=source_type,
        transport="sse",
        format_hint=detect_format_hint(message.data, format_hint),
        raw_payload=message.data,
        transport_metadata=transport_metadata,
        collector_id=collector_id,
    )
    wire = json.dumps(
        event.model_dump(mode="json"),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if max_wire_bytes is not None and len(wire) > max_wire_bytes:
        if stats is not None:
            stats.wire_skips += 1
            if stats.wire_skips > 1:
                return None
        print(
            f"skipped SSE event above the {max_wire_bytes} byte Kafka limit",
            flush=True,
        )
        return None
    try:
        await producer.send_and_wait(raw_topic, wire)
    except MessageSizeTooLargeError:
        if stats is not None:
            stats.broker_skips += 1
            if stats.broker_skips > 1:
                return None
        print("skipped SSE event the broker rejected as too large", flush=True)
        return None
    return event


async def stream_sse(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    last_event_id: str = "",
    max_event_bytes: int = 262_144,
) -> AsyncIterator[SSEMessage]:
    request_headers = dict(headers)
    request_headers["Accept"] = "text/event-stream"
    if last_event_id:
        request_headers["Last-Event-ID"] = last_event_id

    async with client.stream("GET", url, headers=request_headers) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if content_type.strip().lower() != "text/event-stream":
            raise ValueError("upstream response must use text/event-stream")

        parser = SSEParser(
            max_event_bytes=max_event_bytes,
            last_event_id=last_event_id,
        )
        async for chunk in response.aiter_bytes():
            for message in parser.feed(chunk):
                yield message
        for message in parser.close():
            yield message


def validate_upstream_url(
    url: str,
    allowed_hosts: tuple[str, ...] = (),
) -> str:
    if not url or url != url.strip() or any(ord(character) < 32 for character in url):
        raise ValueError("UPSTREAM_SSE_URL must be a valid HTTP(S) URL")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("UPSTREAM_SSE_URL is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise ValueError("UPSTREAM_SSE_URL must be an HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("credentials must not be embedded in UPSTREAM_SSE_URL")

    normalized_hosts = {host.strip().lower() for host in allowed_hosts if host.strip()}
    if not normalized_hosts:
        raise ValueError("SSE_ALLOWED_HOSTS must list at least one upstream host")
    if hostname.lower() not in normalized_hosts:
        raise ValueError("upstream host is not allowed")
    return url


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True)
class SSECollectorConfig:
    upstream_url: str
    source_id: str
    source_type: str
    collector_id: str
    raw_topic: str
    bearer_token: str
    allowed_hosts: tuple[str, ...]
    connect_timeout_seconds: float
    read_timeout_seconds: float
    reconnect_initial_seconds: float
    reconnect_max_seconds: float
    max_event_bytes: int
    max_wire_bytes: int
    format_hint: str
    allow_insecure_http: bool
    retry_max_seconds: float

    def __post_init__(self):
        validate_upstream_url(self.upstream_url, self.allowed_hosts)
        if not self.allowed_hosts:
            raise ValueError("SSE_ALLOWED_HOSTS must list at least one upstream host")
        if (
            self.bearer_token
            and urlsplit(self.upstream_url).scheme.lower() == "http"
            and not self.allow_insecure_http
        ):
            raise ValueError("HTTPS is required when SSE_BEARER_TOKEN is set")
        if not self.source_id or len(self.source_id) > 200:
            raise ValueError("SSE_SOURCE_ID must contain 1 to 200 characters")
        if self.source_type not in VALID_SOURCE_TYPES:
            raise ValueError("SSE_SOURCE_TYPE is not supported")
        if not self.collector_id or not self.raw_topic:
            raise ValueError("collector and topic identifiers cannot be empty")
        if any(ord(character) < 32 for character in self.source_id + self.collector_id):
            raise ValueError("collector identifiers cannot contain control characters")
        if not 1 <= len(self.collector_id) <= 200:
            raise ValueError("SSE_COLLECTOR_ID must contain 1 to 200 characters")
        if not 1 <= len(self.raw_topic) <= 249 or any(
            ord(character) < 32 for character in self.raw_topic
        ):
            raise ValueError("RAW_TOPIC must be 1 to 249 printable characters")
        if self.bearer_token and (
            len(self.bearer_token) > 8192
            or any(ord(character) < 32 for character in self.bearer_token)
        ):
            raise ValueError("SSE_BEARER_TOKEN is invalid")
        timeouts = (
            self.connect_timeout_seconds,
            self.read_timeout_seconds,
            self.reconnect_initial_seconds,
            self.reconnect_max_seconds,
            self.retry_max_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in timeouts):
            raise ValueError("SSE timeouts and reconnect delay must be finite and positive")
        if self.reconnect_max_seconds < self.reconnect_initial_seconds:
            raise ValueError("SSE reconnect maximum cannot be below the initial delay")
        if not 1 <= self.max_event_bytes <= MAX_EVENT_BYTES_LIMIT:
            raise ValueError(
                f"SSE_MAX_EVENT_BYTES must be between 1 and {MAX_EVENT_BYTES_LIMIT}"
            )
        if not 1 <= self.max_wire_bytes <= MAX_WIRE_BYTES_LIMIT:
            raise ValueError(
                f"SSE_MAX_WIRE_BYTES must be between 1 and {MAX_WIRE_BYTES_LIMIT}"
            )
        if self.format_hint not in VALID_FORMAT_HINTS:
            raise ValueError("SSE_FORMAT_HINT is not supported")

    @classmethod
    def from_env(cls) -> "SSECollectorConfig":
        allowed_hosts = tuple(
            host.strip()
            for host in os.getenv("SSE_ALLOWED_HOSTS", "").split(",")
            if host.strip()
        )
        return cls(
            upstream_url=os.getenv("UPSTREAM_SSE_URL", "").strip(),
            source_id=os.getenv(
                "SSE_SOURCE_ID",
                os.getenv("ULPF_SOURCE_ID", "sse-source-1"),
            ).strip(),
            source_type=os.getenv(
                "SSE_SOURCE_TYPE",
                os.getenv("ULPF_SOURCE_TYPE", "application"),
            ).strip(),
            collector_id=os.getenv(
                "SSE_COLLECTOR_ID",
                os.getenv("COLLECTOR_ID", "sse-collector-1"),
            ).strip(),
            raw_topic=os.getenv("RAW_TOPIC", "logs.raw").strip(),
            bearer_token=os.getenv("SSE_BEARER_TOKEN", "").strip(),
            allowed_hosts=allowed_hosts,
            connect_timeout_seconds=_env_float("SSE_CONNECT_TIMEOUT_SECONDS", 10.0),
            read_timeout_seconds=_env_float("SSE_READ_TIMEOUT_SECONDS", 60.0),
            reconnect_initial_seconds=_env_float("SSE_RECONNECT_INITIAL_SECONDS", 1.0),
            reconnect_max_seconds=_env_float("SSE_RECONNECT_MAX_SECONDS", 30.0),
            max_event_bytes=_env_int("SSE_MAX_EVENT_BYTES", 262_144),
            max_wire_bytes=_env_int("SSE_MAX_WIRE_BYTES", 900_000),
            format_hint=os.getenv("SSE_FORMAT_HINT", "auto").strip().lower(),
            allow_insecure_http=_env_bool("SSE_ALLOW_INSECURE_HTTP"),
            retry_max_seconds=_env_float("SSE_RETRY_MAX_SECONDS", 300.0),
        )

    def request_headers(self, last_event_id: str = "") -> dict[str, str]:
        headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "User-Agent": "ulpf-sse-collector/1.0",
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        return headers


StreamFactory = Callable[
    [httpx.AsyncClient, str, dict[str, str], str, int],
    AsyncIterator[SSEMessage],
]


def is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code in {408, 425, 429} or status_code >= 500
    if isinstance(
        exc,
        (
            ValueError,
            httpx.InvalidURL,
            httpx.UnsupportedProtocol,
            MessageSizeTooLargeError,
            UnsupportedVersionError,
            KafkaConfigurationError,
            ProducerClosed,
            RecursionError,
            MemoryError,
        ),
    ):
        return False
    if isinstance(exc, KafkaError):
        return True
    return True


async def _wait_for_retry(stop_event: asyncio.Event, delay: float) -> bool:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
        return True
    except asyncio.TimeoutError:
        return False


async def consume_forever(
    producer: AIOKafkaProducer,
    client: httpx.AsyncClient,
    config: SSECollectorConfig,
    stop_event: Optional[asyncio.Event] = None,
    stream_factory: StreamFactory = stream_sse,
) -> None:
    stop_event = stop_event or asyncio.Event()
    last_event_id = ""
    server_delay: Optional[float] = None
    backoff_delay = config.reconnect_initial_seconds

    while not stop_event.is_set():
        received_data = False
        stats = PublishStats()
        try:
            async for message in stream_factory(
                client=client,
                url=config.upstream_url,
                headers=config.request_headers(last_event_id),
                last_event_id=last_event_id,
                max_event_bytes=config.max_event_bytes,
            ):
                if stop_event.is_set():
                    return
                if message.retry is not None:
                    server_delay = min(
                        max(message.retry / 1000, MIN_RETRY_DELAY_SECONDS),
                        config.retry_max_seconds,
                    )
                if message.data is None:
                    if message.id is not None:
                        last_event_id = message.id
                    continue

                event = await publish_sse_message(
                    producer=producer,
                    message=message,
                    source_id=config.source_id,
                    source_type=config.source_type,
                    collector_id=config.collector_id,
                    raw_topic=config.raw_topic,
                    format_hint=config.format_hint,
                    max_wire_bytes=config.max_wire_bytes,
                    stats=stats,
                )
                if message.id is not None:
                    last_event_id = message.id
                if event is not None:
                    received_data = True

            if stop_event.is_set():
                return
            raise ConnectionError("upstream SSE stream ended")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not is_retryable_error(exc):
                raise
            if server_delay is None:
                delay = backoff_delay
            else:
                delay = max(server_delay, backoff_delay)
            backoff_delay = min(
                max(backoff_delay * 2, MIN_RETRY_DELAY_SECONDS),
                config.reconnect_max_seconds,
            )
            print(
                f"SSE connection failed ({type(exc).__name__}); "
                f"reconnecting in {delay:g}s"
                f" wire_skips={stats.wire_skips}"
                f" broker_skips={stats.broker_skips}",
                flush=True,
            )
            if await _wait_for_retry(stop_event, delay):
                return
            if received_data:
                backoff_delay = config.reconnect_initial_seconds


async def main() -> None:
    config = SSECollectorConfig.from_env()
    producer = AIOKafkaProducer(
        bootstrap_servers=os.getenv("REDPANDA_BROKER", "redpanda:29092"),
        enable_idempotence=True,
        max_request_size=config.max_wire_bytes + 65_536,
    )
    await producer.start()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    try:
        timeout = httpx.Timeout(
            config.read_timeout_seconds,
            connect=config.connect_timeout_seconds,
        )
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            print("SSE collector started", flush=True)
            await consume_forever(
                producer, client, config, stop_event=stop_event
            )
    finally:
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())
