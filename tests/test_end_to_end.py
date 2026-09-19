from orchestrator.main import normalize_raw_event
from schema.raw_event import RawEventEnvelope


def test_syslog_raw_event_is_normalized_for_silver_index():
	raw_event = RawEventEnvelope(
		event_id="raw-1",
		source_id="syslog-source-1",
		source_type="server",
		transport="syslog",
		format_hint="syslog",
		raw_payload="<34>Sep 16 18:40:00 test-server sshd: ULPF TEST MESSAGE",
		collector_id="syslog-collector-1",
	)

	normalized = normalize_raw_event(raw_event)

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
