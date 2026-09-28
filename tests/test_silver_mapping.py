import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "orchestrator", _ROOT / "api"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from orchestrator.main import (  # noqa: E402
    BLOB_AS_KEYWORD,
    SILVER_MAPPING,
    create_dlq_record,
)
from schema.raw_event import RawEventEnvelope  # noqa: E402


def _envelope(payload: str, hint: str = "json") -> RawEventEnvelope:
    return RawEventEnvelope(
        event_id="11111111-1111-1111-1111-111111111111",
        source_id="src-1",
        source_type="application",
        transport="sse",
        format_hint=hint,
        raw_payload=payload,
        collector_id="c-1",
    )


def test_original_json_is_forced_to_keyword():
    assert BLOB_AS_KEYWORD["mapping"]["type"] == "keyword"
    assert BLOB_AS_KEYWORD["path_match"] == "extensions.original_json.*"


def test_silver_mapping_declares_the_blob_template_and_time():
    templates = SILVER_MAPPING["mappings"]["dynamic_templates"]
    assert any(
        t.get("original_json_as_keyword") == BLOB_AS_KEYWORD for t in templates
    )
    assert SILVER_MAPPING["mappings"]["properties"]["time"]["type"] == "date"


def test_dlq_classification_still_resolves_per_format_hint():
    unknown = create_dlq_record(_envelope("free text", hint="unknown"), ["a"])
    assert unknown.classification == "format_unidentified"
    assert unknown.status == "unknown"

    no_match = create_dlq_record(_envelope('{"a":', hint="json"), ["json-parser-v1"])
    assert no_match.classification == "no_parser_match"
    assert no_match.status == "parse_failure"


def test_dlq_accepts_explicit_index_rejection():
    record = create_dlq_record(
        _envelope('{"level": "notice"}', hint="json"),
        ["json-parser-v1"],
        classification="index_rejected",
        status="index_failure",
    )
    assert record.classification == "index_rejected"
    assert record.status == "index_failure"
    assert record.raw_payload == '{"level": "notice"}'
