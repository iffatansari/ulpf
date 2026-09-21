"""
Unit tests for the Module 2 File Collector.

These tests exercise the real streaming logic with fake Kafka/OpenSearch
clients, so they run without Docker, Redpanda or OpenSearch.
"""

import asyncio
import json
from pathlib import Path

import pytest

from schema.raw_event import RawEventEnvelope

import collectors.file_collector.main as fc


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


class FakeES:
    def __init__(self):
        self.updated = []

    def update(self, index, id, body, **kwargs):
        self.updated.append((index, id, body.get("doc", {})))
        return {"result": "updated"}


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "line,expected",
    [
        ("CEF:0|vendor|product|1.0|100|event|5|", "cef"),
        ("<34>Oct 11 22:14:15 host su: msg", "syslog"),
        ('{"user":"alice","action":"login"}', "json"),
        ("[1,2,3]", "json"),
        ("{not valid json", "unknown"),
        ("plain unstructured text", "unknown"),
    ],
)
def test_detect_format_hint(line, expected):
    assert fc.detect_format_hint(line) == expected


def test_sanitize_filename_strips_paths():
    assert fc.sanitize_filename("../../etc/passwd") == "passwd"
    assert fc.sanitize_filename("a/b/c.log") == "c.log"
    assert fc.sanitize_filename("weird<>name.log") == "weird_name.log"
    assert fc.sanitize_filename("") == "upload.log"
    # Any directory component must be neutralised on every platform.
    for separator in ("/", "\\"):
        assert separator not in fc.sanitize_filename(f"a{separator}b.log")


def test_resolve_within_upload_dir_rejects_traversal(tmp_path):
    base = tmp_path / "uploads"
    base.mkdir()
    (base / "ok.log").write_text("x\n")

    good = fc.resolve_within_upload_dir(str(base), "ok.log")
    assert good.name == "ok.log"

    with pytest.raises(ValueError):
        fc.resolve_within_upload_dir(str(base), "../secret.log")

    with pytest.raises(ValueError):
        fc.resolve_within_upload_dir(str(base), str(tmp_path / "outside.log"))


def test_run_upload_streams_every_non_blank_line(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    log = upload_dir / "sample.log"
    log.write_text(
        '<34>Oct 11 22:14:15 host app: hello\n'
        "\n"
        '{"user":"alice"}\n'
        "CEF:0|vendor|prod|1.0|100|evt|5|\n"
        "   \n"
        "garbage line\n"
    )

    monkeypatch.setattr(fc, "UPLOAD_DIR", str(upload_dir))
    producer = FakeProducer()
    es = FakeES()

    result = run(
        fc.run_upload(
            producer,
            es,
            upload_id="u-1",
            file_path="sample.log",
            source_id="src-1",
            source_type="application",
        )
    )

    assert result["total_lines"] == 6
    assert result["blank_lines"] == 2
    assert result["published_events"] == 4
    assert result["line_errors"] == 0
    assert result["status"] == "completed"

    assert len(producer.sent) == 4
    for msg in producer.sent:
        assert msg["topic"] == fc.RAW_TOPIC
        assert ("upload_id", b"u-1") in msg["headers"]
        env = RawEventEnvelope(**msg["value"])
        assert env.transport == "file"
        assert env.source_id == "src-1"
        assert env.source_type == "application"
        assert env.collector_id == fc.COLLECTOR_ID

    formats = [m["value"]["format_hint"] for m in producer.sent]
    assert formats == ["syslog", "json", "cef", "unknown"]

    statuses = [d.get("status") for _, _, d in es.updated]
    assert "processing" in statuses
    assert statuses[-1] == "completed"


def test_run_upload_rejects_path_outside_upload_dir(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    outside = tmp_path / "outside.log"
    outside.write_text("secret\n")

    monkeypatch.setattr(fc, "UPLOAD_DIR", str(upload_dir))
    es = FakeES()

    result = run(
        fc.run_upload(
            FakeProducer(),
            es,
            upload_id="u-2",
            file_path="../outside.log",
            source_id="src-1",
            source_type="application",
        )
    )

    assert result["status"] == "failed"
    assert es.updated[-1][2]["status"] == "failed"


def test_run_upload_isolates_per_line_publish_errors(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "x.log").write_text("line one\nline two\n")

    monkeypatch.setattr(fc, "UPLOAD_DIR", str(upload_dir))

    class Flaky(FakeProducer):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def send_and_wait(self, topic, value, headers=None):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient")
            return await super().send_and_wait(topic, value, headers)

    producer = Flaky()
    es = FakeES()
    result = run(
        fc.run_upload(
            producer,
            es,
            upload_id="u-3",
            file_path="x.log",
            source_id="src-1",
            source_type="application",
        )
    )

    assert result["line_errors"] == 1
    assert result["published_events"] == 1
    assert result["status"] == "completed"
    assert len(producer.sent) == 1


def test_run_upload_handles_invalid_bytes_without_aborting(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    raw = b"good line\n\xff\xfe bad bytes\nanother good\n"
    (upload_dir / "b.log").write_bytes(raw)

    monkeypatch.setattr(fc, "UPLOAD_DIR", str(upload_dir))
    producer = FakeProducer()
    result = run(
        fc.run_upload(
            producer,
            FakeES(),
            upload_id="u-4",
            file_path="b.log",
            source_id="src-1",
            source_type="application",
        )
    )

    assert result["status"] == "completed"
    assert result["published_events"] == 3
    assert len(producer.sent) == 3


def test_run_upload_blank_only_file_fails_with_reason(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "blank.log").write_text("\n   \n\n")

    monkeypatch.setattr(fc, "UPLOAD_DIR", str(upload_dir))
    es = FakeES()

    result = run(
        fc.run_upload(
            FakeProducer(),
            es,
            upload_id="u-5",
            file_path="blank.log",
            source_id="src-1",
            source_type="application",
        )
    )

    assert result["status"] == "failed"
    assert result["published_events"] == 0
    assert result["blank_lines"] == 3
    final = es.updated[-1][2]
    assert final["status"] == "failed"
    assert final["failure_reason"] == "no non-blank lines to process"
