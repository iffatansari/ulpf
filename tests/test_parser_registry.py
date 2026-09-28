"""
Parser registry tests.

The registry is metadata and control over the orchestrator's real parser
chain, not a second implementation of it. The load-bearing test here is
test_registry_matches_the_parsers_the_pipeline_actually_runs: if the registry
drifts from orchestrator/main.py it becomes another source of fiction, which
is the failure mode this feature exists to remove.
"""

import pytest

from orchestrator.parsers import registry
from schema.parser_record import (
    PARSER_STATUSES,
    ParserCreate,
    ParserRecord,
    ParserTestRequest,
    ParserUpdate,
)
from tests.test_dlq_reprocessing import FakeES

PARSERS_INDEX = "ulpf-parsers"

# A line each real parser genuinely accepts.
CEF_LINE = (
    "CEF:0|Fortinet|FortiGate|7.2.0|100|allowed|5|"
    "src=10.0.0.5 dst=10.0.0.9 spt=54321 dpt=443 proto=tcp act=allow"
)
SYSLOG_5424_LINE = '<34>1 2024-01-22T12:42:48Z web1 sshd 2321 - - login failed'
SYSLOG_3164_LINE = "<34>Sep 16 18:40:00 web1 sshd[2211]: Accepted password"
JSON_LINE = '{"@timestamp":"2024-01-22T12:42:48Z","src_ip":"10.0.0.9","msg":"reset"}'
UNPARSEABLE = "%%%% not a log at all &&& ###"


class RegistryES(FakeES):
    """FakeES plus the delete op the parser routes need."""

    def delete(self, index, id, **kwargs):
        existed = self.data.get(index, {}).pop(id, None) is not None
        return {"result": "deleted" if existed else "not_found", "_id": id}


@pytest.fixture
def es():
    return RegistryES(**{PARSERS_INDEX: {}})


# ---------------------------------------------------------------------------
# The registry must describe the chain that actually runs
# ---------------------------------------------------------------------------


def test_registry_matches_the_parsers_the_pipeline_actually_runs():
    """
    Anti-drift: every parser_id the orchestrator can report must be in the
    registry, and vice versa.
    """
    from orchestrator import main as orchestrator_main

    # Pull the ids straight out of the chain definition rather than a
    # hand-kept copy, so this test fails when the chain changes.
    src = orchestrator_main.__file__
    with open(src, encoding="utf-8") as handle:
        body = handle.read()

    import re

    chain_ids = set(re.findall(r'"([a-z0-9-]+-parser-v\d|drain3-fallback-v\d)"', body))

    assert chain_ids, "failed to discover parser ids from the orchestrator chain"
    assert set(registry.BUILTIN_PARSER_IDS) == chain_ids


def test_builtin_parsers_are_exactly_the_four_that_exist():
    # Not seven. LEEF, key=value and Apache/Nginx have no implementation;
    # orchestrator/parsers/generic_parser.py is an empty file.
    assert set(registry.BUILTIN_PARSER_IDS) == {
        "cef-parser-v1",
        "json-parser-v1",
        "syslog-parser-v1",
        "drain3-fallback-v1",
    }


def test_run_parser_executes_the_real_parser():
    event = registry.run_parser(
        "syslog-parser-v1", SYSLOG_5424_LINE, "raw-1", "src-1"
    )

    assert event is not None
    assert event.parser_id == "syslog-parser-v1"
    assert event.app_id == "sshd"
    assert event.severity == "critical"


def test_run_parser_returns_none_when_the_parser_declines():
    event = registry.run_parser("cef-parser-v1", UNPARSEABLE, "raw-1", "src-1")

    assert event is None


def test_run_parser_rejects_an_unknown_id():
    with pytest.raises(KeyError):
        registry.run_parser("leef-parser-v1", CEF_LINE, "raw-1", "src-1")


def test_builtin_records_point_at_callables_that_exist():
    for parser_id, spec in registry.BUILTIN_PARSERS.items():
        assert callable(spec["fn"]), parser_id


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


def test_parser_record_defaults():
    record = ParserRecord(
        parser_id="custom-1",
        display_name="Custom",
    )

    assert record.version == 1
    assert record.status == "draft"
    assert record.is_builtin is False
    assert record.priority == 100


def test_parser_record_rejects_an_unknown_status():
    with pytest.raises(ValueError):
        ParserRecord(parser_id="custom-1", display_name="Custom", status="banana")


def test_parser_create_rejects_a_blank_display_name():
    with pytest.raises(ValueError):
        ParserCreate(parser_id="custom-1", display_name="   ")


def test_parser_create_rejects_a_malformed_id():
    with pytest.raises(ValueError):
        ParserCreate(parser_id="has spaces/and slashes", display_name="Custom")


def test_parser_update_can_change_description():
    update = ParserUpdate(description="new text")

    assert update.description == "new text"


def test_parser_update_ignores_fields_it_does_not_declare():
    # parser_id and is_builtin must not be settable through an update.
    update = ParserUpdate(**{"parser_id": "hijack", "is_builtin": True})

    assert not hasattr(update, "parser_id")
    assert not hasattr(update, "is_builtin")


def test_statuses_are_the_documented_set():
    assert set(PARSER_STATUSES) == {"active", "disabled", "draft"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.fixture
def routes(monkeypatch, es):
    from routes import parsers as parsers_routes

    monkeypatch.setattr(parsers_routes, "get_opensearch_client", lambda: es)
    return parsers_routes


def test_seeding_registers_every_builtin(routes, es):
    routes.ensure_builtin_parsers()

    stored = set(es.data[PARSERS_INDEX])
    assert stored == set(routes.registry.BUILTIN_PARSER_IDS)


def test_seeding_is_idempotent_and_keeps_operator_edits(routes, es):
    routes.ensure_builtin_parsers()
    routes.update_parser(
        "cef-parser-v1",
        ParserUpdate(description="operator edited this"),
    )

    routes.ensure_builtin_parsers()

    record = routes.list_parsers()[0]
    assert record["description"] == "operator edited this", (
        "re-seeding must not clobber a deliberate operator change"
    )
    assert len(es.data[PARSERS_INDEX]) == len(routes.registry.BUILTIN_PARSER_IDS)


def test_seeding_marks_builtins_as_builtin_and_undeletable(routes, es):
    routes.ensure_builtin_parsers()

    records = {r["parser_id"]: r for r in routes.list_parsers()}
    assert records["cef-parser-v1"]["is_builtin"] is True
    assert records["cef-parser-v1"]["status"] == "active"


def test_list_parsers_is_ordered_by_priority_then_name(routes):
    routes.ensure_builtin_parsers()
    routes.create_parser(
        ParserCreate(
            parser_id="aaa-early",
            display_name="Early",
            priority=1,
            status="active",
        )
    )

    ids = [r["parser_id"] for r in routes.list_parsers()]
    assert ids[0] == "aaa-early"


def test_list_parsers_can_hide_disabled(routes):
    routes.ensure_builtin_parsers()
    routes.update_parser("cef-parser-v1", ParserUpdate(status="disabled"))

    visible = [r["parser_id"] for r in routes.list_parsers()]
    everything = [r["parser_id"] for r in routes.list_parsers(include_disabled=True)]

    assert "cef-parser-v1" not in visible
    assert "cef-parser-v1" in everything


def test_create_parser_stores_a_new_record(routes):
    created = routes.create_parser(
        ParserCreate(
            parser_id="nginx-combined",
            display_name="Nginx combined",
            description="two-space delimited",
            field_rules=[{"name": "status", "pattern": r"\d{3}"}],
        )
    )

    assert created["parser_id"] == "nginx-combined"
    assert created["status"] == "draft"
    assert created["field_rules"] == [{"name": "status", "pattern": r"\d{3}"}]


def test_create_parser_rejects_a_duplicate_id(routes):
    routes.create_parser(ParserCreate(parser_id="dupe", display_name="One"))

    with pytest.raises(Exception) as exc:
        routes.create_parser(ParserCreate(parser_id="dupe", display_name="Two"))
    assert getattr(exc.value, "status_code", None) == 409


def test_create_parser_will_not_shadow_a_builtin(routes):
    routes.ensure_builtin_parsers()

    with pytest.raises(Exception) as exc:
        routes.create_parser(
            ParserCreate(parser_id="cef-parser-v1", display_name="Impostor")
        )
    assert getattr(exc.value, "status_code", None) == 409


def test_get_parser_returns_none_for_an_unknown_id(routes):
    assert routes.get_parser("nope") is None


def test_update_parser_bumps_updated_at(routes):
    routes.create_parser(ParserCreate(parser_id="p1", display_name="One"))
    before = routes.get_parser("p1")["updated_at"]

    updated = routes.update_parser("p1", ParserUpdate(display_name="Renamed"))

    assert updated["display_name"] == "Renamed"
    assert updated["updated_at"] >= before


def test_update_parser_rejects_an_unknown_id(routes):
    with pytest.raises(Exception) as exc:
        routes.update_parser("nope", ParserUpdate(display_name="x"))
    assert getattr(exc.value, "status_code", None) == 404


def test_builtin_can_be_disabled_but_not_deleted(routes):
    routes.ensure_builtin_parsers()

    disabled = routes.update_parser(
        "cef-parser-v1", ParserUpdate(status="disabled")
    )
    assert disabled["status"] == "disabled"

    with pytest.raises(Exception) as exc:
        routes.delete_parser("cef-parser-v1")
    assert getattr(exc.value, "status_code", None) == 409


def test_delete_parser_removes_a_custom_parser(routes):
    routes.create_parser(ParserCreate(parser_id="temp", display_name="Temp"))

    routes.delete_parser("temp")

    assert routes.get_parser("temp") is None


def test_delete_parser_rejects_an_unknown_id(routes):
    with pytest.raises(Exception) as exc:
        routes.delete_parser("nope")
    assert getattr(exc.value, "status_code", None) == 404


# ---------------------------------------------------------------------------
# /parsers/test
# ---------------------------------------------------------------------------


def test_test_endpoint_runs_the_real_parser(routes):
    result = routes.test_parser(
        ParserTestRequest(
            parser_id="syslog-parser-v1",
            sample=SYSLOG_5424_LINE,
        )
    )

    assert result["matched"] is True
    assert result["event"]["parser_id"] == "syslog-parser-v1"
    assert result["event"]["app_id"] == "sshd"
    assert result["event"]["severity"] == "critical"


def test_test_endpoint_reports_a_clean_miss(routes):
    result = routes.test_parser(
        ParserTestRequest(parser_id="cef-parser-v1", sample=UNPARSEABLE)
    )

    assert result["matched"] is False
    assert result["event"] is None
    assert "reason" in result


def test_test_endpoint_rejects_an_unknown_parser(routes):
    with pytest.raises(Exception) as exc:
        routes.test_parser(
            ParserTestRequest(parser_id="leef-parser-v1", sample=CEF_LINE)
        )
    assert getattr(exc.value, "status_code", None) == 404


def test_test_endpoint_rejects_an_empty_sample(routes):
    with pytest.raises(Exception) as exc:
        routes.test_parser(ParserTestRequest(parser_id="cef-parser-v1", sample="  "))
    assert getattr(exc.value, "status_code", None) == 422


def test_test_endpoint_refuses_a_custom_parser_it_cannot_execute(routes):
    """
    A custom parser is declarative field_rules, not code. The endpoint must
    say so rather than silently returning a false negative that reads as
    'this parser does not work'.
    """
    routes.create_parser(
        ParserCreate(
            parser_id="custom-kv",
            display_name="Key value",
            field_rules=[{"name": "user", "pattern": r'user=(\S+)'}],
        )
    )

    result = routes.test_parser(
        ParserTestRequest(parser_id="custom-kv", sample="user=bob action=login")
    )

    assert result["matched"] is True
    assert result["extracted"] == {"user": "bob"}
    assert result["event"] is None
    assert "declarative" in result["reason"]


def test_test_endpoint_records_the_result_on_the_parser(routes):
    routes.ensure_builtin_parsers()

    routes.test_parser(
        ParserTestRequest(parser_id="cef-parser-v1", sample=CEF_LINE)
    )

    stored = routes.get_parser("cef-parser-v1")
    assert stored["last_test"]["matched"] is True
