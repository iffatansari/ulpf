"""
Full-pipeline reconciliation test (no live services).

Drives the REAL demo file through the REAL File Collector streaming code
and the REAL orchestrator parsing/normalization code, with only the I/O
sides (Kafka producer, OpenSearch) faked:

    demo/mixed_security_logs.log
      → file_collector.run_upload (line-by-line, per-line hints)
      → RawEventEnvelope per accepted line
      → orchestrator: Bronze persistence → parser selection → Silver/DLQ

Then asserts Module 2's accounting invariant:

    non-blank lines == published events == Bronze raw events
    Bronze raw events == normalized events + DLQ events

and that every Silver/DLQ raw_event_id resolves to the same Bronze
document (lossless lineage). Mirrors exactly what the live Docker stack
is supposed to produce, but runs without Redpanda/OpenSearch.
"""

import asyncio
import json
from pathlib import Path

from schema.raw_event import RawEventEnvelope

import collectors.file_collector.main as fc
from orchestrator.main import create_dlq_record, normalize_raw_event

DEMO_LOG = Path(__file__).resolve().parent.parent / "demo" / "mixed_security_logs.log"

BRONZE_INDEX = "ulpf-bronze"
SILVER_INDEX = "ulpf-silver"
DLQ_INDEX = "ulpf-dlq"

SOURCE_ID = "demo-security-file-abc123"
SOURCE_TYPE = "server"
UPLOAD_ID = "upload-reconcile-1"


class FakeProducer:
    def __init__(self):
        self.messages = []

    async def send_and_wait(self, topic, value, headers=None):
        self.messages.append(
            {
                "topic": topic,
                "value": json.loads(value.decode("utf-8")),
                "headers": headers or [],
            }
        )


class FakeES:
    """In-memory OpenSearch fake: stores by (index, id) and counts."""

    def __init__(self):
        self.store = {}
        self.uploads = []

    def _key(self, index, id):
        return (index, id)

    def index(self, index, id, body):
        self.store[self._key(index, id)] = body

    def update(self, index, id, body, **kwargs):
        key = self._key(index, id)
        doc = dict(self.store.get(key, {}))
        doc.update(body["doc"])
        self.store[key] = doc
        return {"result": "updated"}


def run(coro):
    return asyncio.run(coro)


def process_as_orchestrator(es: FakeES, producer_snapshot):
    """Mirror orchestrator/main.py's per-message logic exactly."""
    bronze = 0
    silver = 0
    dlq = 0
    for msg in producer_snapshot:
        raw_event = RawEventEnvelope(**msg["value"])
        upload_id = None
        for key, value in msg["headers"]:
            if key == "upload_id":
                upload_id = value.decode("utf-8")
        raw_doc = raw_event.model_dump(mode="json")
        if upload_id:
            raw_doc["upload_id"] = upload_id

        es.index(index=BRONZE_INDEX, id=raw_event.event_id, body=raw_doc)
        bronze += 1

        normalized, parsers_attempted = normalize_raw_event(raw_event)
        if normalized is not None:
            es.index(
                index=SILVER_INDEX,
                id=normalized.event_id,
                body=normalized.model_dump(mode="json"),
            )
            silver += 1
        else:
            dlq_record = create_dlq_record(raw_event, parsers_attempted)
            es.index(
                index=DLQ_INDEX,
                id=dlq_record.dlq_id,
                body=dlq_record.model_dump(mode="json"),
            )
            dlq += 1

    return bronze, silver, dlq


def test_module2_pipeline_reconciliation(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    upload_dir.joinpath("mixed_security_logs.log").write_bytes(
        DEMO_LOG.read_bytes()
    )

    monkeypatch.setattr(fc, "UPLOAD_DIR", str(upload_dir))
    producer = FakeProducer()
    es = FakeES()

    result = run(
        fc.run_upload(
            producer,
            es,
            upload_id=UPLOAD_ID,
            file_path="mixed_security_logs.log",
            source_id=SOURCE_ID,
            source_type=SOURCE_TYPE,
        )
    )

    # --- File Collector accounting --------------------------------
    assert result["status"] == "completed"
    assert result["total_lines"] == 4000
    assert result["blank_lines"] == 4
    nonblank = result["total_lines"] - result["blank_lines"]
    assert result["published_events"] == nonblank == 3996
    assert result["line_errors"] == 0

    produced = producer.messages
    assert len(produced) == 3996
    for msg in produced:
        assert msg["topic"] == fc.RAW_TOPIC
        assert ("upload_id", UPLOAD_ID.encode()) in msg["headers"]
        RawEventEnvelope(**msg["value"])  # schema-valid envelope
        assert msg["value"]["transport"] == "file"
        assert msg["value"]["source_id"] == SOURCE_ID
        assert msg["value"]["source_type"] == SOURCE_TYPE

    fmts = {}
    for msg in produced:
        h = msg["value"]["format_hint"]
        fmts[h] = fmts.get(h, 0) + 1
    # Actual demo distribution — see demo/generate_mixed_log.py.
    assert fmts["syslog"] == 1953
    assert fmts["json"] == 1014
    assert fmts["cef"] == 618
    assert fmts["unknown"] == 411

    # --- Orchestrator accounting ---------------------------------
    bronze, silver, dlq = process_as_orchestrator(es, produced)

    assert bronze == nonblank == 3996
    assert bronze == silver + dlq == 3585 + 411
    assert silver == 3585  # syslog + json + cef
    assert dlq == 411      # unknown lines dropped to DLQ

    # --- Bronze stores upload_id lineage metadata -----------------
    bronze_docs = [d for (idx, _), d in es.store.items() if idx == BRONZE_INDEX]
    assert len(bronze_docs) == bronze
    assert all(d.get("upload_id") == UPLOAD_ID for d in bronze_docs)

    # --- Lineage: every Silver and DLQ resolves to a Bronze doc ----
    silver_docs = [d for (idx, _), d in es.store.items() if idx == SILVER_INDEX]
    dlq_docs = [d for (idx, _), d in es.store.items() if idx == DLQ_INDEX]

    for doc in silver_docs:
        raw = es.store[(BRONZE_INDEX, doc["raw_event_id"])]
        assert raw is not None
        assert doc["source_id"] if doc.get("source_id") else True

    for doc in dlq_docs:
        raw = es.store[(BRONZE_INDEX, doc["raw_event_id"])]
        assert raw is not None
        assert raw["raw_payload"] == doc["raw_payload"]


def test_module2_lineage_payload_round_trip(tmp_path, monkeypatch):
    """A Silver event's raw_event_id returns the verbatim raw payload."""
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    log = upload_dir / "mix.log"
    log.write_text(
        "CEF:0|V|P|1.0|100|BLOCKED|5|src=10.0.0.1 dst=10.0.0.2\n"
        "<34>Sep 19 12:20:00 server1 sshd: Accepted login for alice\n"
    )
    monkeypatch.setattr(fc, "UPLOAD_DIR", str(upload_dir))
    producer = FakeProducer()
    es = FakeES()

    run(
        fc.run_upload(
            producer,
            es,
            upload_id="u-l1",
            file_path="mix.log",
            source_id=SOURCE_ID,
            source_type=SOURCE_TYPE,
        )
    )

    process_as_orchestrator(es, producer.messages)

    assert len(producer.messages) == 2
    silver = [d for (idx, _), d in es.store.items() if idx == SILVER_INDEX]
    assert len(silver) == 2

    for m in producer.messages:
        raw_id = m["value"]["event_id"]
        norm = [
            d
            for d in silver
            if d["raw_event_id"] == raw_id
        ][0]
        bronze = es.store[(BRONZE_INDEX, raw_id)]
        assert bronze["raw_payload"] == m["value"]["raw_payload"]
        assert norm["raw_event_id"] == bronze["event_id"]

    # CEF normalized with endpoints; syslog normalized with device/app.
    by_parser = {d["parser_id"] for d in silver}
    assert by_parser == {"cef-parser-v1", "syslog-parser-v1"}