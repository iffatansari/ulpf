from orchestrator.parsers.syslog_parser import parse_syslog


def test_parse_syslog_with_pri():
	event = parse_syslog(
		"<34>Sep 16 18:40:00 test-server sshd: ULPF TEST MESSAGE",
		raw_event_id="raw-1",
		source_id="syslog-source-1",
	)

	assert event is not None
	assert event.event_id == "raw-1-norm"
	assert event.parser_id == "syslog-parser-v1"
	assert event.parser_tier == "primary"
	assert event.device_id == "test-server"
	assert event.app_id == "sshd"
	assert event.extensions["syslog_message"] == "ULPF TEST MESSAGE"
	assert (event.time.month, event.time.day, event.time.hour, event.time.minute, event.time.second) == (
		9,
		16,
		18,
		40,
		0,
	)
