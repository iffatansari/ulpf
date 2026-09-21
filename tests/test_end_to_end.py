from datetime import datetime, timezone

import orchestrator.main as main_module
from orchestrator.main import normalize_raw_event
from schema.normalized_event import NormalizedEvent
from schema.raw_event import RawEventEnvelope


def test_syslog_raw_event_is_normalized_for_silver_index():
	raw_event = RawEventEnvelope(
		event_id="raw-1",
		source_id="syslog-source-1",
		source_type="server",
		transport="udp",
		format_hint="syslog",
		raw_payload="<34>Sep 16 18:40:00 test-server sshd: ULPF TEST MESSAGE",
		collector_id="syslog-collector-1",
	)

normalized, _ = normalize_raw_event(raw_event)

	assert normalized is not None
	assert normalized.event_id == "raw-1-norm"
	assert normalized.raw_event_id == "raw-1"
	assert normalized.parser_id == "syslog-parser-v1"
	assert normalized.parser_tier == "primary"
	assert normalized.device_id == "test-server"
	assert normalized.app_id == "sshd"
	assert (normalized.time.month, normalized.time.day, normalized.time.hour, normalized.time.minute) == (
		9,
		16,
		18,
		40,
	)


def test_unknown_event_uses_drain3_fallback_after_known_parsers_fail(monkeypatch):
	raw_event = RawEventEnvelope(
		event_id="raw-unknown-1",
		source_id="edge-source-1",
		source_type="network_device",
		transport="udp",
		format_hint="unknown",
		raw_payload="unknown log event with 10.0.0.1 to 10.0.0.2 user alice",
		collector_id="collector-1",
	)

	monkeypatch.setattr(main_module, "parse_cef_log", lambda *_args, **_kwargs: None)
	monkeypatch.setattr(main_module, "parse_json_log", lambda *_args, **_kwargs: None)
	monkeypatch.setattr(main_module, "parse_syslog", lambda *_args, **_kwargs: None)
	monkeypatch.setattr(
		main_module,
		"parse_drain",
		lambda *_args, **_kwargs: NormalizedEvent(
			event_id="raw-unknown-1-norm",
			raw_event_id="raw-unknown-1",
			parser_id="drain3-fallback-v1",
			parser_tier="drain3",
			confidence_score=0.5,
			schema_id="ulpc-ocsf-v1",
			schema_version="1.0.0",
			class_name="network_activity",
			category_name="network",
			activity_name="unknown",
			severity="low",
			time=datetime.now(timezone.utc),
			user="alice",
			device_id="edge-source-1",
			app_id=None,
			src_endpoint="10.0.0.1",
			dst_endpoint="10.0.0.2",
			protocol_name=None,
			action="unknown",
			extensions={},
		),
	)

	normalized, parsers_attempted = main_module.normalize_raw_event(raw_event)

	assert normalized is not None
	assert normalized.parser_id == "drain3-fallback-v1"
	assert normalized.parser_tier == "drain3"
	assert parsers_attempted == [
		"cef-parser-v1",
		"json-parser-v1",
		"syslog-parser-v1",
		"drain3-fallback-v1",
	]


def test_unknown_event_goes_to_dlq_when_drain3_fails(monkeypatch):
	raw_event = RawEventEnvelope(
		event_id="raw-unknown-2",
		source_id="edge-source-2",
		source_type="application",
		transport="http",
		format_hint="unknown",
		raw_payload="this payload is not recognized by any parser",
		collector_id="collector-2",
	)

	monkeypatch.setattr(main_module, "parse_cef_log", lambda *_args, **_kwargs: None)
	monkeypatch.setattr(main_module, "parse_json_log", lambda *_args, **_kwargs: None)
	monkeypatch.setattr(main_module, "parse_syslog", lambda *_args, **_kwargs: None)
	monkeypatch.setattr(main_module, "parse_drain", lambda *_args, **_kwargs: None)

	normalized, parsers_attempted = main_module.normalize_raw_event(raw_event)

	assert normalized is None
	assert parsers_attempted == [
		"cef-parser-v1",
		"json-parser-v1",
		"syslog-parser-v1",
		"drain3-fallback-v1",
	]
	assert main_module.create_dlq_record(raw_event, parsers_attempted).classification == "format_unidentified"


def test_real_drain3_preserves_port_when_port_value_changes():
    from orchestrator.parsers.drain_fallback import parse_drain

    first = parse_drain(
        "firewall src=10.0.0.1 dst=10.0.0.2 user=alice action=denied port=443",
        "real-drain-1",
        "test-source",
    )

    second = parse_drain(
        "firewall src=10.0.0.5 dst=10.0.0.8 user=bob action=accepted port=22",
        "real-drain-2",
        "test-source",
    )

    assert first is not None
    assert second is not None

    assert first.parser_id == "drain3-fallback-v1"
    assert second.parser_id == "drain3-fallback-v1"

    assert first.src_endpoint == "10.0.0.1"
    assert first.dst_endpoint == "10.0.0.2"
    assert first.user == "alice"
    assert first.action == "deny"
    assert first.extensions["port"] == "443"

    assert second.src_endpoint == "10.0.0.5"
    assert second.dst_endpoint == "10.0.0.8"
    assert second.user == "bob"
    assert second.action == "allow"
    assert second.extensions["port"] == "22"
