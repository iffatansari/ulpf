from orchestrator.parsers.json_parser import parse_json_log
from orchestrator.parsers.cef_parser import parse_cef_log
from orchestrator.parsers.severity import (
	MAX_SYSLOG_PRI,
	SEVERITY_KEYWORDS,
	SEVERITY_TEXT_LEVELS,
	SEVEREITY_ORDER,
	SYSLOG_SEVERITY_TO_NORMALIZED,
	severity_from_cef_number,
	severity_from_message,
	severity_from_number,
	severity_from_text,
	syslog_severity_from_pri,
)
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


def test_syslog_pri_severity_wins_over_message_keywords():
	event = parse_syslog(
		"<34>Sep 16 18:41:00 fw-edge-01 CRON: session opened for bob",
		raw_event_id="raw-2",
		source_id="syslog-source-1",
	)

	assert event is not None
	assert event.severity == "critical"
	assert event.app_id == "CRON"


def test_syslog_keeps_keyword_inference_when_pri_is_absent():
	event = parse_syslog(
		"Sep 16 18:42:00 fw-edge-01 sshd: authentication fail for alice",
		raw_event_id="raw-3",
		source_id="syslog-source-1",
	)

	assert event is not None
	assert event.severity == "high"


def test_syslog_info_pri_is_not_upgraded_by_keywords():
	event = parse_syslog(
		"<38>Sep 16 18:43:00 fw-edge-01 CRON: session opened",
		raw_event_id="raw-4",
		source_id="syslog-source-1",
	)

	assert event is not None
	assert event.severity == "low"


def test_syslog_strips_pid_from_app_id_and_keeps_it_in_extensions():
	event = parse_syslog(
		"<34>Sep 16 18:40:04 fw-edge-01 sshd[2211]: Accepted password for alice",
		raw_event_id="raw-5",
		source_id="syslog-source-1",
	)

	assert event is not None
	assert event.app_id == "sshd"
	assert event.extensions["syslog_pid"] == 2211


def test_syslog_without_pid_has_no_pid_extension():
	event = parse_syslog(
		"<34>Sep 16 18:40:00 test-server sshd: ULPF TEST MESSAGE",
		raw_event_id="raw-6",
		source_id="syslog-source-1",
	)

	assert event is not None
	assert "syslog_pid" not in event.extensions


def test_syslog_pri_facility_and_severity_are_both_exposed():
	event = parse_syslog(
		"<34>Sep 16 18:40:00 test-server sshd: ULPF TEST MESSAGE",
		raw_event_id="raw-7",
		source_id="syslog-source-1",
	)

	assert event is not None
	assert event.extensions["syslog_facility"] == "auth"
	assert event.extensions["syslog_severity_code"] == "crit"


def test_json_maps_numeric_level_on_the_syslog_scale():
	severities = {}
	for level in range(8):
		event = parse_json_log(
			'{"timestamp": "2026-09-16T18:40:07Z", "host": "h1", "level": %d}' % level,
			raw_event_id="raw-json-%d" % level,
			source_id="json-source-1",
		)
		assert event is not None
		severities[level] = event.severity

	assert severities[0] == "critical"
	assert severities[2] == "critical"
	assert severities[3] == "high"
	assert severities[4] == "medium"
	assert severities[6] == "low"
	assert severities[7] == "low"


def test_json_numeric_severity_field_is_mapped_too():
	event = parse_json_log(
		'{"timestamp": "2026-09-16T18:40:08Z", "host": "h1", "severity": 3}',
		raw_event_id="raw-json-sev",
		source_id="json-source-1",
	)

	assert event is not None
	assert event.severity == "high"


def test_json_numeric_string_level_is_mapped():
	event = parse_json_log(
		'{"timestamp": "2026-09-16T18:40:08Z", "host": "h1", "level": "2"}',
		raw_event_id="raw-json-str",
		source_id="json-source-1",
	)

	assert event is not None
	assert event.severity == "critical"


def test_json_out_of_range_numbers_do_not_invent_severity():
	event = parse_json_log(
		'{"timestamp": "2026-09-16T18:40:08Z", "host": "h1", "level": 42}',
		raw_event_id="raw-json-oor",
		source_id="json-source-1",
	)

	assert event is not None
	assert event.severity == "low"


def test_json_text_severity_behaviour_is_unchanged():
	higher = parse_json_log(
		'{"timestamp": "2026-09-16T18:40:08Z", "level": "ERROR"}',
		raw_event_id="raw-json-error",
		source_id="json-source-1",
	)
	medium = parse_json_log(
		'{"timestamp": "2026-09-16T18:40:08Z", "level": "WARNING"}',
		raw_event_id="raw-json-warn",
		source_id="json-source-1",
	)

	assert higher is not None and higher.severity == "high"
	assert medium is not None and medium.severity == "medium"


def test_severity_from_number_handles_non_numeric_input():
	assert severity_from_number(None) is None
	assert severity_from_number("not-a-number") is None
	assert severity_from_number(3.5) is None
	assert severity_from_number(True) is None


def test_syslog_severity_from_pri_decodes_facility_and_severity():
	assert syslog_severity_from_pri(34) == "critical"
	assert syslog_severity_from_pri(167) == "low"
	assert syslog_severity_from_pri(132) == "medium"
	assert syslog_severity_from_pri(131) == "high"
	assert syslog_severity_from_pri(-1) is None
	assert syslog_severity_from_pri(999) is None


def test_json_text_critical_maps_to_critical():
	event = parse_json_log(
		'{"timestamp": "2026-09-16T18:40:08Z", "level": "critical"}',
		raw_event_id="raw-json-crit",
		source_id="json-source-1",
	)

	assert event is not None
	assert event.severity == "critical"


def test_json_text_severity_vocabulary():
	expected = {
		"EMERG": "critical",
		"panic": "critical",
		"fatal": "critical",
		"alert": "critical",
		"crit": "critical",
		"critical": "critical",
		"err": "high",
		"error": "high",
		"severe": "high",
		"warn": "medium",
		"warning": "medium",
		"notice": "low",
		"info": "low",
		"informational": "low",
		"debug": "low",
		"banana": "low",
	}

	for token, level in expected.items():
		event = parse_json_log(
			'{"timestamp": "2026-09-16T18:40:08Z", "level": "%s"}' % token,
			raw_event_id="raw-json-%s" % token,
			source_id="json-source-1",
		)
		assert event is not None
		assert event.severity == level, token


def test_severity_vocabulary_is_identical_across_parsers():
	critical_syslog = parse_syslog(
		"<34>Sep 16 18:41:00 host app: anything",
		raw_event_id="raw-unify-crit-syslog",
		source_id="s1",
	)
	critical_json_text = parse_json_log(
		'{"timestamp": "2026-09-16T18:40:08Z", "level": "critical"}',
		raw_event_id="raw-unify-crit-text",
		source_id="s1",
	)
	critical_json_number = parse_json_log(
		'{"timestamp": "2026-09-16T18:40:08Z", "level": 2}',
		raw_event_id="raw-unify-crit-number",
		source_id="s1",
	)
	critical_cef = parse_cef_log(
		"CEF:0|Vendor|Product|1.0|100|Name|9|src=10.0.0.1",
		raw_event_id="raw-unify-crit-cef",
		source_id="s1",
	)
	high_syslog = parse_syslog(
		"<131>Sep 16 18:41:00 host app: anything",
		raw_event_id="raw-unify-high-syslog",
		source_id="s1",
	)
	high_json = parse_json_log(
		'{"timestamp": "2026-09-16T18:40:08Z", "level": "error"}',
		raw_event_id="raw-unify-high-text",
		source_id="s1",
	)
	high_cef = parse_cef_log(
		"CEF:0|Vendor|Product|1.0|100|Name|6|src=10.0.0.1",
		raw_event_id="raw-unify-high-cef",
		source_id="s1",
	)

	for event in (
		critical_syslog,
		critical_json_text,
		critical_json_number,
		critical_cef,
	):
		assert event is not None
		assert event.severity == "critical"
	for event in (high_syslog, high_json, high_cef):
		assert event is not None
		assert event.severity == "high"


def test_severity_from_text_rejects_unknown_and_non_text():
	assert severity_from_text("banana") is None
	assert severity_from_text(7) is None
	assert severity_from_text(None) is None
	assert severity_from_text("CRITICAL") == "critical"


def test_severity_from_message_takes_the_most_severe_keyword():
	assert severity_from_message("fatal unrecoverable error") == "critical"
	assert severity_from_message("warning: disk almost full") == "medium"
	assert severity_from_message("authentication failure") == "high"
	assert severity_from_message("connection reset by peer") is None


def test_syslog_keyword_fallback_uses_the_unified_vocabulary():
	event = parse_syslog(
		"Sep 16 18:42:00 host app: fatal unrecoverable error",
		raw_event_id="raw-syslog-keyword",
		source_id="s1",
	)

	assert event is not None
	assert event.severity == "critical"



def test_severity_from_cef_number_uses_the_cef_scale():
	assert severity_from_cef_number(10) == "critical"
	assert severity_from_cef_number(9) == "critical"
	assert severity_from_cef_number(8) == "critical"
	assert severity_from_cef_number(7) == "high"
	assert severity_from_cef_number(5) == "high"
	assert severity_from_cef_number(4) == "medium"
	assert severity_from_cef_number(3) == "medium"
	assert severity_from_cef_number(1) == "low"
	assert severity_from_cef_number(0) == "low"
	assert severity_from_cef_number("banana") is None
	assert severity_from_cef_number(None) is None


def test_cef_severity_tokens_are_understood():
	for token, level in (
		("High", "high"),
		("Low", "low"),
		("Critical", "critical"),
		("Medium", "medium"),
	):
		event = parse_cef_log(
			"CEF:0|Vendor|Product|1.0|100|Name|%s|src=10.0.0.1" % token,
			raw_event_id="raw-cef-%s" % token,
			source_id="s1",
		)
		assert event is not None
		assert event.severity == level


def test_cef_unknown_severity_still_defaults_to_medium():
	event = parse_cef_log(
		"CEF:0|Vendor|Product|1.0|100|Name|banana|src=10.0.0.1",
		raw_event_id="raw-cef-banana",
		source_id="s1",
	)

	assert event is not None
	assert event.severity == "medium"



def test_every_supported_token_maps_into_the_four_level_vocabulary():
	tokens = list(SEVERITY_TEXT_LEVELS.values())

	assert set(tokens) <= set(SEVEREITY_ORDER)
	assert set(SEVERITY_TEXT_LEVELS) >= {"low", "medium", "high", "critical"}
	assert set(SYSLOG_SEVERITY_TO_NORMALIZED.values()) <= set(SEVEREITY_ORDER)
	assert {level for _, level in SEVERITY_KEYWORDS} <= set(SEVEREITY_ORDER)


