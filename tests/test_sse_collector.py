import asyncio
import json

import httpx
import pytest
from aiokafka.errors import (
    KafkaConfigurationError,
    MessageSizeTooLargeError,
    ProducerClosed,
    RequestTimedOutError,
    UnsupportedVersionError,
)

from orchestrator.main import normalize_raw_event
from schema.raw_event import RawEventEnvelope

import collectors.sse_collector.main as sse


class FakeProducer:
    def __init__(self):
        self.sent = []

    async def send_and_wait(self, topic, value, headers=None):
        self.sent.append(
            {
                "topic": topic,
                "value": json.loads(value.decode("utf-8")),
                "headers": headers or [],
            }
        )


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        return None


def run(coro):
    return asyncio.run(coro)


def make_config(**overrides):
    values = {
        "upstream_url": "https://events.example.test/stream",
        "source_id": "sse-source-1",
        "source_type": "application",
        "collector_id": "sse-collector-1",
        "raw_topic": "logs.raw",
        "bearer_token": "",
        "allowed_hosts": ("events.example.test",),
        "connect_timeout_seconds": 1.0,
        "read_timeout_seconds": 5.0,
        "reconnect_initial_seconds": 0.001,
        "reconnect_max_seconds": 0.001,
        "retry_max_seconds": 300.0,
        "max_event_bytes": 1024,
        "max_wire_bytes": 900_000,
        "format_hint": "auto",
        "allow_insecure_http": False,
    }
    values.update(overrides)
    return sse.SSECollectorConfig(**values)


def test_parser_handles_chunked_multiline_events_and_comments():
    parser = sse.SSEParser(max_event_bytes=1024, last_event_id="previous")

    assert parser.feed(b": heartbeat\r\nid: event-7\r\nevent: update\r\ndata: {\r\n") == []
    messages = parser.feed(
        b'data: "time":"2026-09-25T10:00:00Z",\r\ndata: "user":"amy"}\r\n\r\n'
    )

    assert len(messages) == 1
    assert messages[0].data == '{\n"time":"2026-09-25T10:00:00Z",\n"user":"amy"}'
    assert messages[0].event == "update"
    assert messages[0].id == "event-7"
    assert messages[0].retry is None


def test_parser_returns_retry_controls_and_honours_id_reset():
    parser = sse.SSEParser(max_event_bytes=1024)

    messages = parser.feed(b"retry: 2500\n\nid:\ndata: reset\n\n")

    assert messages[0].data is None
    assert messages[0].retry == 2500
    assert messages[1].data == "reset"
    assert messages[1].id == ""


def test_parser_skips_oversized_frames_without_desynchronizing():
    parser = sse.SSEParser(max_event_bytes=12)

    messages = parser.feed(
        b"data: this frame is too large\n\ndata: ok\n\n"
    )

    assert len(messages) == 1
    assert messages[0].data == "ok"


def test_parser_discards_unterminated_final_event():
    parser = sse.SSEParser(max_event_bytes=1024)

    assert parser.feed(b"data: final") == []

    assert parser.close() == []


@pytest.mark.parametrize(
    "payload,expected",
    [
        ('{"time":"2026-09-25T10:00:00Z"}', "json"),
        ("CEF:0|vendor|product|1.0|100|event|5|", "cef"),
        ("<34>Oct 11 22:14:15 host app: hello", "syslog"),
        ("not structured", "unknown"),
    ],
)
def test_detect_format_hint(payload, expected):
    assert sse.detect_format_hint(payload) == expected


def test_publish_sse_message_enters_normalization_pipeline():
    producer = FakeProducer()
    message = sse.SSEMessage(
        data='{"time":"2026-09-25T10:00:00Z","user":"amy","action":"login"}',
        event="activity",
        id="upstream-42",
        retry=1500,
    )

    raw_event = run(
        sse.publish_sse_message(
            producer=producer,
            message=message,
            source_id="sse-source-1",
            source_type="application",
            collector_id="sse-collector-1",
            raw_topic="logs.raw",
        )
    )

    assert raw_event is not None
    assert producer.sent[0]["topic"] == "logs.raw"
    envelope = RawEventEnvelope(**producer.sent[0]["value"])
    assert envelope.transport == "sse"
    assert envelope.format_hint == "json"
    assert envelope.transport_metadata == {
        "sse_event_id": "upstream-42",
        "sse_event_type": "activity",
    }

    normalized, attempted = normalize_raw_event(envelope)
    assert attempted == ["json-parser-v1"]
    assert normalized is not None
    assert normalized.user == "amy"
    assert normalized.extensions["source_id"] == "sse-source-1"


def test_publish_sse_message_ignores_control_events_only():
    producer = FakeProducer()

    assert (
        run(
            sse.publish_sse_message(
                producer=producer,
                message=sse.SSEMessage(data=None),
                source_id="sse-source-1",
                source_type="application",
                collector_id="sse-collector-1",
                raw_topic="logs.raw",
            )
        )
        is None
    )
    assert producer.sent == []


def test_publish_sse_message_keeps_done_payload_visible():
    producer = FakeProducer()

    raw_event = run(
        sse.publish_sse_message(
            producer=producer,
            message=sse.SSEMessage(data="[DONE]"),
            source_id="sse-source-1",
            source_type="application",
            collector_id="sse-collector-1",
            raw_topic="logs.raw",
        )
    )

    assert raw_event is not None
    assert producer.sent[0]["value"]["raw_payload"] == "[DONE]"
    assert producer.sent[0]["value"]["format_hint"] == "unknown"


def test_stream_sse_sends_auth_and_resume_headers():
    captured = {}

    async def handler(request):
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            stream=ChunkStream(
                [
                    b"id: 100\ndata: {\"time\":\"2026-09-25T10:00:00Z\"}\n\n",
                    b"data: {\"time\":\"2026-09-25T10:00:01Z\"}\n\n",
                ]
            ),
        )

    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [
                message
                async for message in sse.stream_sse(
                    client=client,
                    url="https://events.example.test/stream",
                    headers={"Authorization": "Bearer secret"},
                    last_event_id="99",
                    max_event_bytes=1024,
                )
            ]

    messages = run(collect())

    assert captured["headers"]["authorization"] == "Bearer secret"
    assert captured["headers"]["last-event-id"] == "99"
    assert [message.data for message in messages] == [
        '{"time":"2026-09-25T10:00:00Z"}',
        '{"time":"2026-09-25T10:00:01Z"}',
    ]
    assert messages[1].id == "100"


def test_stream_sse_rejects_non_event_stream_content_type():
    async def handler(request):
        return httpx.Response(200, headers={"content-type": "application/json"})

    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [
                message
                async for message in sse.stream_sse(
                    client=client,
                    url="https://events.example.test/stream",
                    headers={},
                    max_event_bytes=1024,
                )
            ]

    with pytest.raises(ValueError, match="text/event-stream"):
        run(collect())


@pytest.mark.parametrize(
    "url,allowed_hosts",
    [
        ("ftp://events.example.test/stream", ()),
        ("https://user:pass@events.example.test/stream", ()),
        ("https://events.example.test/stream", ("other.example.test",)),
        ("https://events.example.test:bad/stream", ()),
    ],
)
def test_validate_upstream_url_rejects_unsafe_targets(url, allowed_hosts):
    with pytest.raises(ValueError):
        sse.validate_upstream_url(url, allowed_hosts)


def test_validate_upstream_url_accepts_https_allowlisted_host():
    assert (
        sse.validate_upstream_url(
            "https://events.example.test/stream",
            ("events.example.test",),
        )
        == "https://events.example.test/stream"
    )


def test_parser_handles_split_bom_and_rejects_use_after_close():
    parser = sse.SSEParser(max_event_bytes=1024)

    assert parser.feed(sse.BOM_UTF8[:1]) == []
    messages = parser.feed(sse.BOM_UTF8[1:] + b"data: ready\n\n")
    parser.close()

    assert messages[0].data == "ready"
    with pytest.raises(RuntimeError, match="closed"):
        parser.feed(b"data: late\n\n")


@pytest.mark.parametrize(
    "field",
    [
        "connect_timeout_seconds",
        "read_timeout_seconds",
        "reconnect_initial_seconds",
        "reconnect_max_seconds",
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_config_rejects_non_finite_timeouts(field, value):
    with pytest.raises(ValueError, match="timeouts"):
        make_config(**{field: value})


def test_config_from_env_builds_authenticated_headers(monkeypatch):
    monkeypatch.setenv("UPSTREAM_SSE_URL", " https://events.example.test/stream ")
    monkeypatch.setenv("SSE_SOURCE_ID", "source-42")
    monkeypatch.setenv("SSE_SOURCE_TYPE", "application")
    monkeypatch.setenv("SSE_COLLECTOR_ID", "collector-42")
    monkeypatch.setenv("RAW_TOPIC", "logs.raw")
    monkeypatch.setenv("SSE_BEARER_TOKEN", "secret")
    monkeypatch.setenv(
        "SSE_ALLOWED_HOSTS",
        "events.example.test, backup.example.test",
    )
    monkeypatch.setenv("SSE_MAX_EVENT_BYTES", "2048")

    config = sse.SSECollectorConfig.from_env()

    assert config.upstream_url == "https://events.example.test/stream"
    assert config.allowed_hosts == (
        "events.example.test",
        "backup.example.test",
    )
    assert config.request_headers("event-9") == {
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
        "User-Agent": "ulpf-sse-collector/1.0",
        "Authorization": "Bearer secret",
        "Last-Event-ID": "event-9",
    }


@pytest.mark.parametrize(
    "status,expected",
    [(200, False), (408, True), (429, True), (503, True)],
)
def test_retryable_error_classifies_http_statuses(status, expected):
    request = httpx.Request("GET", "https://events.example.test/stream")
    response = httpx.Response(status, request=request)
    error = httpx.HTTPStatusError("status", request=request, response=response)

    assert sse.is_retryable_error(error) is expected


@pytest.mark.parametrize(
    "error,expected",
    [
        (MessageSizeTooLargeError("too big", 1, 2, 3), False),
        (UnsupportedVersionError("unsupported", 2), False),
        (KafkaConfigurationError("bad config"), False),
        (ProducerClosed("closed"), False),
        (RequestTimedOutError("timed out"), True),
    ],
)
def test_retryable_error_classifies_kafka_failures(error, expected):
    assert sse.is_retryable_error(error) is expected


def test_consume_never_advances_resume_id_before_kafka_ack():
    class FailingProducer(FakeProducer):
        async def send_and_wait(self, topic, value, headers=None):
            raise RuntimeError("Kafka unavailable")

    async def scenario():
        stop_event = asyncio.Event()
        seen_resume_ids = []

        async def stream_factory(
            client,
            url,
            headers,
            last_event_id,
            max_event_bytes,
        ):
            seen_resume_ids.append(last_event_id)
            if len(seen_resume_ids) == 1:
                yield sse.SSEMessage(
                    data='{"time":"2026-09-25T10:00:00Z"}',
                    id="event-2",
                )
                raise ConnectionError("stream ended")
            stop_event.set()
            if False:
                yield None

        await sse.consume_forever(
            producer=FailingProducer(),
            client=object(),
            config=make_config(),
            stop_event=stop_event,
            stream_factory=stream_factory,
        )
        return seen_resume_ids

    assert run(scenario()) == ["", ""]


def test_consume_tracks_control_id_and_stops_on_done():
    async def scenario():
        stop_event = asyncio.Event()
        seen_resume_ids = []

        async def stream_factory(
            client,
            url,
            headers,
            last_event_id,
            max_event_bytes,
        ):
            seen_resume_ids.append(last_event_id)
            if len(seen_resume_ids) == 1:
                yield sse.SSEMessage(data=None, id="control-2")
                return
            stop_event.set()
            if False:
                yield None

        await sse.consume_forever(
            producer=FakeProducer(),
            client=object(),
            config=make_config(),
            stop_event=stop_event,
            stream_factory=stream_factory,
        )
        return seen_resume_ids

    assert run(scenario()) == ["", "control-2"]


def test_consume_does_not_retry_permanent_stream_errors():
    async def stream_factory(
        client,
        url,
        headers,
        last_event_id,
        max_event_bytes,
    ):
        if False:
            yield None
        raise ValueError("invalid content type")

    with pytest.raises(ValueError, match="content type"):
        run(
            sse.consume_forever(
                producer=FakeProducer(),
                client=object(),
                config=make_config(),
                stream_factory=stream_factory,
            )
        )


def test_main_starts_and_stops_collector_dependencies(monkeypatch):
    started = {"producer": False, "stopped": False, "consumed": False}
    config = make_config()

    class FakeProducer:
        def __init__(self, bootstrap_servers, **kwargs):
            assert bootstrap_servers == "redpanda:29092"
            assert kwargs["enable_idempotence"] is True
            assert kwargs["max_request_size"] == config.max_wire_bytes + 65_536

        async def start(self):
            started["producer"] = True

        async def stop(self):
            started["stopped"] = True

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["timeout"].read == config.read_timeout_seconds

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def fake_consume(producer, client, received_config, stop_event=None):
        assert producer is not None
        assert client is not None
        assert received_config == config
        assert isinstance(stop_event, asyncio.Event)
        started["consumed"] = True

    monkeypatch.setattr(sse, "AIOKafkaProducer", FakeProducer)
    monkeypatch.setattr(sse.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        sse.SSECollectorConfig,
        "from_env",
        classmethod(lambda cls: config),
    )
    monkeypatch.setattr(sse, "consume_forever", fake_consume)

    run(sse.main())

    assert started == {
        "producer": True,
        "stopped": True,
        "consumed": True,
    }


def test_parser_bounds_unterminated_line_and_resynchronizes():
    parser = sse.SSEParser(max_event_bytes=32)

    assert parser.feed(b"data: " + b"x" * 1024) == []
    assert len(parser._line) <= 33

    messages = parser.feed(b"\n\ndata: recovered\n\n")

    assert [message.data for message in messages] == ["recovered"]


def test_parser_ignores_hostile_retry_and_non_ascii_id():
    parser = sse.SSEParser(max_event_bytes=1024)

    messages = parser.feed(
        b"retry: " + b"9" * 5000 + b"\nid: caf\xe9-1\ndata: safe\n\n"
    )

    assert len(messages) == 1
    assert messages[0].data == "safe"
    assert messages[0].id is None
    assert messages[0].retry is None


def test_config_requires_explicit_host_allowlist():
    with pytest.raises(ValueError, match="SSE_ALLOWED_HOSTS"):
        make_config(allowed_hosts=())


def test_config_requires_https_for_bearer_authentication():
    with pytest.raises(ValueError, match="HTTPS"):
        make_config(
            upstream_url="http://events.example.test/stream",
            bearer_token="secret",
        )

    config = make_config(
        upstream_url="http://events.example.test/stream",
        bearer_token="secret",
        allow_insecure_http=True,
    )

    assert config.allow_insecure_http is True


def test_server_retry_survives_a_data_event(monkeypatch):
    delays = []

    async def scenario():
        stop_event = asyncio.Event()

        async def stream_factory(
            client,
            url,
            headers,
            last_event_id,
            max_event_bytes,
        ):
            yield sse.SSEMessage(data=None, retry=2000)
            yield sse.SSEMessage(
                data='{"time":"2026-09-25T10:00:00Z"}',
                id="event-2",
            )
            raise ConnectionError("stream ended")

        async def capture_delay(stop, delay):
            delays.append(delay)
            stop.set()
            return True

        monkeypatch.setattr(sse, "_wait_for_retry", capture_delay)
        await sse.consume_forever(
            producer=FakeProducer(),
            client=object(),
            config=make_config(reconnect_max_seconds=5.0),
            stop_event=stop_event,
            stream_factory=stream_factory,
        )

    run(scenario())

    assert delays == [2.0]


def test_consume_continues_after_done_payload():
    async def scenario():
        stop_event = asyncio.Event()
        calls = []
        producer = FakeProducer()

        async def stream_factory(
            client,
            url,
            headers,
            last_event_id,
            max_event_bytes,
        ):
            calls.append(last_event_id)
            if len(calls) == 1:
                yield sse.SSEMessage(data="[DONE]")
                return
            stop_event.set()
            if False:
                yield None

        await sse.consume_forever(
            producer=producer,
            client=object(),
            config=make_config(),
            stop_event=stop_event,
            stream_factory=stream_factory,
        )
        return calls, producer

    calls, producer = run(scenario())

    assert calls == ["", ""]
    assert len(producer.sent) == 1


def test_publish_sse_message_assigns_unique_event_ids_for_reused_upstream_ids():
    producer = FakeProducer()

    async def scenario():
        for payload in ('{"time":"2026-09-25T10:00:00Z","n":1}', '{"time":"2026-09-25T10:00:01Z","n":2}'):
            await sse.publish_sse_message(
                producer=producer,
                message=sse.SSEMessage(data=payload, id="reused-1"),
                source_id="sse-source-1",
                source_type="application",
                collector_id="sse-collector-1",
                raw_topic="logs.raw",
            )

    run(scenario())

    event_ids = [record["value"]["event_id"] for record in producer.sent]
    assert len(set(event_ids)) == 2


def test_publish_sse_message_skips_payloads_beyond_the_wire_limit(capsys):
    producer = FakeProducer()

    async def scenario():
        for _ in range(3):
            await sse.publish_sse_message(
                producer=producer,
                message=sse.SSEMessage(data="x" * 4096),
                source_id="sse-source-1",
                source_type="application",
                collector_id="sse-collector-1",
                raw_topic="logs.raw",
                max_wire_bytes=512,
                stats=sse.PublishStats(),
            )

    run(scenario())

    assert producer.sent == []
    assert capsys.readouterr().out.count("above the 512 byte Kafka limit") == 3


def test_consume_throttles_wire_skip_logging_per_connection(capsys):
    async def scenario():
        stop_event = asyncio.Event()
        calls = []

        async def stream_factory(
            client,
            url,
            headers,
            last_event_id,
            max_event_bytes,
        ):
            calls.append(last_event_id)
            if len(calls) == 1:
                for index in range(3):
                    yield sse.SSEMessage(data="y" * 4096, id=f"skipped-{index}")
                raise ConnectionError("stream ended")
            stop_event.set()
            if False:
                yield None

        await sse.consume_forever(
            producer=FakeProducer(),
            client=object(),
            config=make_config(max_wire_bytes=512),
            stop_event=stop_event,
            stream_factory=stream_factory,
        )

    run(scenario())
    output = capsys.readouterr().out

    assert output.count("above the 512 byte Kafka limit") == 1
    assert "wire_skips=3" in output


def test_publish_sse_message_skips_events_the_broker_rejects_as_oversized(
    capsys,
):
    class RejectingProducer(FakeProducer):
        async def send_and_wait(self, topic, value, headers=None):
            raise MessageSizeTooLargeError("too large", 1, 2, 3)

    async def scenario():
        return await sse.publish_sse_message(
            producer=RejectingProducer(),
            message=sse.SSEMessage(data='{"time":"2026-09-25T10:00:00Z"}'),
            source_id="sse-source-1",
            source_type="application",
            collector_id="sse-collector-1",
            raw_topic="logs.raw",
        )

    assert run(scenario()) is None
    assert "broker rejected as too large" in capsys.readouterr().out


def test_consume_survives_a_broker_side_oversize_rejection():
    async def scenario():
        stop_event = asyncio.Event()
        calls = []
        producer = FakeProducer()

        class RejectingProducer(FakeProducer):
            async def send_and_wait(self, topic, value, headers=None):
                if len(calls) == 1:
                    raise MessageSizeTooLargeError("too large", 1, 2, 3)
                return await FakeProducer.send_and_wait(self, topic, value, headers)

        async def stream_factory(
            client,
            url,
            headers,
            last_event_id,
            max_event_bytes,
        ):
            calls.append(last_event_id)
            if len(calls) == 1:
                yield sse.SSEMessage(
                    data='{"time":"2026-09-25T10:00:00Z"}', id="big-1"
                )
                raise ConnectionError("stream ended")
            stop_event.set()
            if False:
                yield None

        await sse.consume_forever(
            producer=RejectingProducer(),
            client=object(),
            config=make_config(),
            stop_event=stop_event,
            stream_factory=stream_factory,
        )
        return calls, producer

    calls, producer = run(scenario())

    assert calls == ["", "big-1"]
    assert len(producer.sent) == 0


def test_parser_drops_a_frame_assembled_from_many_legal_lines():
    parser = sse.SSEParser(max_event_bytes=64)
    frame = b"".join(b"data: " + b"x" * 10 + b"\n" for _ in range(6))

    assert parser.feed(frame + b"\n") == []
    messages = parser.feed(b"data: after\n\n")

    assert [message.data for message in messages] == ["after"]
    assert parser.dropped_frames == 1


def test_consume_keeps_running_after_an_oversized_payload_is_skipped():
    async def scenario():
        stop_event = asyncio.Event()
        calls = []
        producer = FakeProducer()

        async def stream_factory(
            client,
            url,
            headers,
            last_event_id,
            max_event_bytes,
        ):
            calls.append(last_event_id)
            if len(calls) == 1:
                yield sse.SSEMessage(data="y" * 4096, id="skipped-1")
                raise ConnectionError("stream ended")
            stop_event.set()
            if False:
                yield None

        await sse.consume_forever(
            producer=producer,
            client=object(),
            config=make_config(max_wire_bytes=512),
            stop_event=stop_event,
            stream_factory=stream_factory,
        )
        return calls, producer

    calls, producer = run(scenario())

    assert calls == ["", "skipped-1"]
    assert producer.sent == []


def test_client_backoff_still_grows_when_the_server_sends_no_retry(monkeypatch):
    delays = []

    async def scenario():
        stop_event = asyncio.Event()

        async def stream_factory(
            client,
            url,
            headers,
            last_event_id,
            max_event_bytes,
        ):
            raise ConnectionError("stream ended")
            yield  # pragma: no cover

        async def capture_delay(stop, delay):
            delays.append(delay)
            if len(delays) == 4:
                stop.set()
                return True
            return False

        monkeypatch.setattr(sse, "_wait_for_retry", capture_delay)
        await sse.consume_forever(
            producer=FakeProducer(),
            client=object(),
            config=make_config(
                reconnect_initial_seconds=1.0,
                reconnect_max_seconds=30.0,
            ),
            stop_event=stop_event,
            stream_factory=stream_factory,
        )

    run(scenario())

    assert delays == [1.0, 2.0, 4.0, 8.0]


def test_server_retry_acts_as_a_floor_on_client_backoff(monkeypatch):
    delays = []

    async def scenario():
        stop_event = asyncio.Event()

        async def stream_factory(
            client,
            url,
            headers,
            last_event_id,
            max_event_bytes,
        ):
            yield sse.SSEMessage(data=None, retry=1000)
            raise ConnectionError("stream ended")

        async def capture_delay(stop, delay):
            delays.append(delay)
            stop.set()
            return True

        monkeypatch.setattr(sse, "_wait_for_retry", capture_delay)
        await sse.consume_forever(
            producer=FakeProducer(),
            client=object(),
            config=make_config(
                reconnect_initial_seconds=1.0,
                reconnect_max_seconds=30.0,
            ),
            stop_event=stop_event,
            stream_factory=stream_factory,
        )

    run(scenario())

    assert delays == [1.0]


def test_server_retry_is_clamped_by_retry_max_seconds(monkeypatch):
    delays = []

    async def scenario():
        stop_event = asyncio.Event()

        async def stream_factory(
            client,
            url,
            headers,
            last_event_id,
            max_event_bytes,
        ):
            yield sse.SSEMessage(data=None, retry=3_600_000)
            raise ConnectionError("stream ended")

        async def capture_delay(stop, delay):
            delays.append(delay)
            stop.set()
            return True

        monkeypatch.setattr(sse, "_wait_for_retry", capture_delay)
        await sse.consume_forever(
            producer=FakeProducer(),
            client=object(),
            config=make_config(
                reconnect_max_seconds=30.0,
                retry_max_seconds=2.0,
            ),
            stop_event=stop_event,
            stream_factory=stream_factory,
        )

    run(scenario())

    assert delays == [2.0]


def test_parser_handles_bare_carriage_returns_split_across_chunks():
    parser = sse.SSEParser(max_event_bytes=1024)

    assert parser.feed(b"data: one\rdata: two\r") == []
    messages = parser.feed(b"\rdata: three\r\r")

    assert [message.data for message in messages] == ["one\ntwo", "three"]


def test_parser_handles_crlf_split_at_the_chunk_boundary():
    parser = sse.SSEParser(max_event_bytes=1024)

    assert parser.feed(b"data: split\r") == []
    messages = parser.feed(b"\n\r\n")

    assert [message.data for message in messages] == ["split"]


def test_parser_does_not_charge_comments_to_the_frame_budget():
    parser = sse.SSEParser(max_event_bytes=16)

    assert parser.feed(b": keepalive\n" * 8) == []
    messages = parser.feed(b"data: kept\n\n")

    assert [message.data for message in messages] == ["kept"]


def test_parser_drops_the_whole_frame_when_a_data_line_is_oversized():
    parser = sse.SSEParser(max_event_bytes=16)

    messages = parser.feed(b"data: 0123456789abcdefghij\ndata: tail\n\n")

    assert messages == []
    assert parser.dropped_frames == 1


def test_parser_reports_dropped_frames_only_once(capsys):
    parser = sse.SSEParser(max_event_bytes=8)

    parser.feed(b"data: 0123456789abcdef\n\ndata: 0123456789abcdef\n\n")
    parser.close()

    assert parser.dropped_frames == 2
    assert capsys.readouterr().out.count("dropped SSE frame") == 1


def test_validate_upstream_url_fails_closed_without_an_allowlist():
    with pytest.raises(ValueError, match="SSE_ALLOWED_HOSTS"):
        sse.validate_upstream_url("https://169.254.169.254/latest/meta-data/", ())


def test_validate_upstream_url_rejects_control_characters():
    with pytest.raises(ValueError):
        sse.validate_upstream_url("https://events.example.test/\r\nx", ())


@pytest.mark.parametrize("value", [0.0, -1.0])
def test_config_rejects_non_positive_timeouts(value):
    with pytest.raises(ValueError, match="timeouts"):
        make_config(read_timeout_seconds=value)


def test_detect_format_hint_survives_deeply_nested_json():
    payload = "[" * 50_000 + "]" * 50_000

    assert sse.detect_format_hint(payload) == "unknown"


def test_detect_format_hint_survives_deeply_nested_objects():
    payload = '{"a":' * 20_000 + "1" + "}" * 20_000

    assert sse.detect_format_hint(payload) in {"json", "unknown"}


@pytest.mark.parametrize(
    "error",
    [RecursionError("deep"), MemoryError()],
)
def test_retryable_error_rejects_resource_exhaustion(error):
    assert sse.is_retryable_error(error) is False


def test_parser_marks_which_events_declared_their_own_id():
    parser = sse.SSEParser(max_event_bytes=1024)

    messages = parser.feed(b"id: sticky\ndata: first\n\ndata: second\n\n")

    assert [message.id for message in messages] == ["sticky", "sticky"]
    assert messages[0].id_in_frame is True
    assert messages[1].id_in_frame is False


def test_publish_omits_sse_event_id_for_inherited_ids():
    producer = FakeProducer()

    async def scenario():
        return await sse.publish_sse_message(
            producer=producer,
            message=sse.SSEMessage(data='{"time":"2026-09-25T10:00:00Z"}', id_in_frame=False),
            source_id="sse-source-1",
            source_type="application",
            collector_id="sse-collector-1",
            raw_topic="logs.raw",
        )

    assert run(scenario()) is not None
    assert "sse_event_id" not in producer.sent[0]["value"]["transport_metadata"]


def test_publish_skeeps_declared_ids():
    producer = FakeProducer()

    async def scenario():
        return await sse.publish_sse_message(
            producer=producer,
            message=sse.SSEMessage(data='{"time":"2026-09-25T10:00:00Z"}', id="own-1"),
            source_id="sse-source-1",
            source_type="application",
            collector_id="sse-collector-1",
            raw_topic="logs.raw",
        )

    assert run(scenario()) is not None
    assert producer.sent[0]["value"]["transport_metadata"] == {"sse_event_id": "own-1"}


def test_config_bounds_event_and_wire_sizes():
    with pytest.raises(ValueError, match="SSE_MAX_EVENT_BYTES"):
        make_config(max_event_bytes=2_000_000_000)

    with pytest.raises(ValueError, match="SSE_MAX_WIRE_BYTES"):
        make_config(max_wire_bytes=2_000_000_000)


def test_config_bounds_collector_and_topic_identifiers():
    with pytest.raises(ValueError, match="SSE_COLLECTOR_ID"):
        make_config(collector_id="c" * 201)

    with pytest.raises(ValueError, match="RAW_TOPIC"):
        make_config(raw_topic="logs.raw\ninjected")

    with pytest.raises(ValueError, match="RAW_TOPIC"):
        make_config(raw_topic="t" * 250)
