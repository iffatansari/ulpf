"""
Custom parser tier: a registered parser actually runs.

Before this, a custom parser could be created, listed, tested and deleted in the
UI and the pipeline would never call it. Both halves of that failure are pinned
here:

  * the API half -- a write that the next read cannot see (OpenSearch is
    near-real-time, and the registry is read through `search`).
  * the chain half -- the orchestrator's tier order, and the fact that a draft
    or disabled parser is deliberately not in it.
"""

import copy

import pytest

from orchestrator import main as orchestrator_main

# The orchestrator runs with `orchestrator/` on sys.path (WORKDIR=/app/orchestrator,
# PYTHONPATH=/app), so main.py's `from parsers import custom_chain` binds the
# top-level module. Importing the `orchestrator.parsers.` alias instead would
# give a second copy of the module with its own cache, and the fixture below
# would clear a cache nothing reads.
from parsers import custom_chain
from schema.raw_event import RawEventEnvelope
from tests.test_dlq_reprocessing import FakeES

PARSERS_INDEX = "ulpf-parsers"

# Nothing built-in claims this line: no CEF header, no JSON, no syslog PRI.
PROXY_LINE = (
    "acme-proxy t=2024-01-22T12:42:48Z user=alice action=login "
    "src=10.1.1.5 dst=10.2.2.9 level=warn request_id=req-77"
)
CEF_LINE = (
    "CEF:0|Fortinet|FortiGate|7.2.0|100|allowed|5|"
    "src=10.0.0.5 dst=10.0.0.9 spt=54321 dpt=443 proto=tcp act=allow"
)

PROXY_RULES = [
    {"name": "time", "pattern": r"t=(\S+)"},
    {"name": "user", "pattern": r"user=(\S+)"},
    {"name": "action", "pattern": r"action=(\S+)"},
    {"name": "src", "pattern": r"src=(\S+)"},
    {"name": "dst", "pattern": r"dst=(\S+)"},
    {"name": "level", "pattern": r"level=(\S+)"},
    # No schema field of this name: it must survive in extensions.
    {"name": "request_id", "pattern": r"request_id=(\S+)"},
]


def proxy_record(**overrides):
    """A registry record for the proxy parser, as the API would store it."""
    record = {
        "parser_id": "acme-proxy-v2",
        "display_name": "Acme proxy v2",
        "status": "active",
        "priority": 10,
        "is_builtin": False,
        "version": 1,
        "field_rules": copy.deepcopy(PROXY_RULES),
    }
    record.update(overrides)
    return record


def raw_event(payload=PROXY_LINE, hint="acme"):
    return RawEventEnvelope(
        event_id="raw-1",
        source_id="src-1",
        source_type="network_device",
        transport="other",
        collector_id="col-1",
        format_hint=hint,
        raw_payload=payload,
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    """
    The loaded parser set is process-wide, so a test that leaves it behind
    would decide what the next one sees.
    """
    custom_chain.clear_cache()
    yield
    custom_chain.clear_cache()


@pytest.fixture
def registry_es():
    return FakeES(**{PARSERS_INDEX: {}})


# ---------------------------------------------------------------------------
# The chain actually calls a registered custom parser
# ---------------------------------------------------------------------------


def test_active_custom_parser_normalizes_a_line_no_builtin_claimed(
    registry_es,
):
    registry_es.data[PARSERS_INDEX]["acme-proxy-v2"] = proxy_record()

    normalized, attempted = orchestrator_main.normalize_raw_event(
        raw_event(), registry_es
    )

    assert normalized is not None, "a registered active parser was not tried"
    assert normalized.parser_id == "acme-proxy-v2"
    assert normalized.parser_tier == "custom"
    assert "acme-proxy-v2" in attempted


def test_custom_parser_runs_before_the_drain3_fallback(registry_es):
    """
    If Drain3 got there first it would claim the line and label it by template,
    which looks identical, to the operator, to the parser not working.
    """
    registry_es.data[PARSERS_INDEX]["acme-proxy-v2"] = proxy_record()

    normalized, attempted = orchestrator_main.normalize_raw_event(
        raw_event(), registry_es
    )

    assert normalized.parser_id == "acme-proxy-v2"
    assert "drain3-fallback-v1" not in attempted


def test_a_builtin_still_wins_over_a_custom_parser(registry_es):
    """
    A custom parser is for a format the chain does not understand. It must not
    take a line that a real parser already decoded.
    """
    registry_es.data[PARSERS_INDEX]["acme-proxy-v2"] = proxy_record(
        field_rules=[{"name": "src", "pattern": r"src=(\S+)"}]
    )

    normalized, attempted = orchestrator_main.normalize_raw_event(
        raw_event(payload=CEF_LINE, hint="cef"), registry_es
    )

    assert normalized.parser_id == "cef-parser-v1"
    assert "acme-proxy-v2" not in attempted


def test_a_draft_custom_parser_is_stored_but_not_parsed_with(registry_es):
    """
    The API creates every parser as a draft so a half-written rule set cannot
    start consuming traffic on save. So a draft must be inert, and saying so
    loudly is the difference between "wrong" and "not yet".
    """
    registry_es.data[PARSERS_INDEX]["acme-proxy-v2"] = proxy_record(status="draft")

    normalized, attempted = orchestrator_main.normalize_raw_event(
        raw_event(), registry_es
    )

    assert "acme-proxy-v2" not in attempted
    assert normalized.parser_id == "drain3-fallback-v1", (
        "a draft must not parse; the fallback should still have its turn"
    )


def test_a_disabled_custom_parser_is_not_parsed_with(registry_es):
    registry_es.data[PARSERS_INDEX]["acme-proxy-v2"] = proxy_record(status="disabled")

    _normalized, attempted = orchestrator_main.normalize_raw_event(
        raw_event(), registry_es
    )

    assert "acme-proxy-v2" not in attempted


def test_activating_a_custom_parser_puts_it_in_the_chain(registry_es):
    """
    The whole point of the status flag: a draft that becomes active is parsed
    without the orchestrator being restarted.
    """
    record = proxy_record(status="draft")
    registry_es.data[PARSERS_INDEX]["acme-proxy-v2"] = record

    _normalized, attempted = orchestrator_main.normalize_raw_event(
        raw_event(), registry_es
    )
    assert "acme-proxy-v2" not in attempted

    record["status"] = "active"
    custom_chain.clear_cache()

    normalized, attempted = orchestrator_main.normalize_raw_event(
        raw_event(), registry_es
    )
    assert "acme-proxy-v2" in attempted
    assert normalized.parser_id == "acme-proxy-v2"


def test_the_custom_tier_never_re_runs_a_builtin(registry_es):
    """
    Built-ins are in the registry as metadata. The custom tier reads the
    registry, so without the is_builtin guard every line would be parsed twice.
    """
    registry_es.data[PARSERS_INDEX]["cef-parser-v1"] = {
        "parser_id": "cef-parser-v1",
        "status": "active",
        "is_builtin": True,
        "field_rules": [{"name": "src", "pattern": r"src=(\S+)"}],
    }

    assert custom_chain.custom_parsers(registry_es, force=True) == []


def test_custom_parsers_are_tried_in_priority_order(registry_es):
    registry_es.data[PARSERS_INDEX] = {
        "acme-proxy-v2": proxy_record(parser_id="acme-proxy-v2", priority=50),
        "acme-proxy-v2-first": proxy_record(
            parser_id="acme-proxy-v2-first", priority=1
        ),
    }

    normalized, _attempted = orchestrator_main.normalize_raw_event(
        raw_event(), registry_es
    )

    assert normalized.parser_id == "acme-proxy-v2-first"


def test_an_unreadable_registry_does_not_break_parsing():
    """
    The built-in chain has already run by the time the custom tier is reached,
    and Drain3 still follows. A control-plane failure must not become a
    data-plane failure.
    """

    class BrokenES:
        def search(self, *args, **kwargs):
            raise RuntimeError("index_not_found_exception")

    _normalized, attempted = orchestrator_main.normalize_raw_event(
        raw_event(), BrokenES()
    )

    assert "drain3-fallback-v1" in attempted


def test_the_registry_is_read_once_per_ttl_not_once_per_event(registry_es):
    """
    A burst of events must not become a burst of registry reads.
    """
    registry_es.data[PARSERS_INDEX]["acme-proxy-v2"] = proxy_record()

    calls = {"search": 0}
    original_search = registry_es.search

    def counting_search(*args, **kwargs):
        calls["search"] += 1
        return original_search(*args, **kwargs)

    registry_es.search = counting_search

    for _ in range(5):
        orchestrator_main.normalize_raw_event(raw_event(), registry_es)

    assert calls["search"] == 1


# ---------------------------------------------------------------------------
# What the custom parser extracts reaches the Silver schema
# ---------------------------------------------------------------------------


def test_recognised_rule_names_land_on_schema_fields(registry_es):
    registry_es.data[PARSERS_INDEX]["acme-proxy-v2"] = proxy_record()

    normalized, _attempted = orchestrator_main.normalize_raw_event(
        raw_event(), registry_es
    )

    assert normalized.user == "alice"
    assert normalized.action == "login"
    assert normalized.src_endpoint == "10.1.1.5"
    assert normalized.dst_endpoint == "10.2.2.9"
    assert normalized.severity == "medium", '"warn" is a known severity token'
    assert normalized.time.year == 2024, "the extracted timestamp must be used"


def test_the_common_short_time_names_are_understood(registry_es):
    """
    `t=` and `ts=` are what vendor lines call a timestamp, and a rule named
    after the key in the log is what an operator will write.
    """
    for rule_name in ("t", "ts", "time", "datetime"):
        registry_es.data[PARSERS_INDEX]["acme-proxy-v2"] = proxy_record(
            field_rules=[{"name": rule_name, "pattern": r"t=(\S+)"}]
        )
        custom_chain.clear_cache()

        normalized, _attempted = orchestrator_main.normalize_raw_event(
            raw_event(), registry_es
        )

        assert normalized.time.year == 2024, rule_name
        assert rule_name not in normalized.extensions, rule_name


def test_fields_the_schema_has_no_home_for_are_preserved(registry_es):
    registry_es.data[PARSERS_INDEX]["acme-proxy-v2"] = proxy_record()

    normalized, _attempted = orchestrator_main.normalize_raw_event(
        raw_event(), registry_es
    )

    assert normalized.extensions["request_id"] == "req-77", (
        "a field the rule set asked for must never be silently dropped"
    )
    assert normalized.extensions["source_id"] == "src-1"
    # `level` and `t` are consumed into severity and time rather than copied
    # into extensions, so the schema field is the only place to look for them.
    assert "level" not in normalized.extensions
    assert "t" not in normalized.extensions


def test_a_custom_parser_that_matches_nothing_declines(registry_es):
    """
    A rule set that extracts nothing is not this format, and saying so is what
    lets the chain move on instead of inventing an event.
    """
    registry_es.data[PARSERS_INDEX]["acme-proxy-v2"] = proxy_record(
        field_rules=[{"name": "nope", "pattern": r"never=appears"}]
    )

    normalized, attempted = orchestrator_main.normalize_raw_event(
        raw_event(), registry_es
    )

    assert normalized.parser_id == "drain3-fallback-v1"
    assert "acme-proxy-v2" in attempted


def test_an_unparseable_timestamp_falls_back_to_ingestion_time(registry_es):
    registry_es.data[PARSERS_INDEX]["acme-proxy-v2"] = proxy_record(
        field_rules=[{"name": "time", "pattern": r"t=(\S+)"}]
    )

    normalized, _attempted = orchestrator_main.normalize_raw_event(
        raw_event(payload="acme-proxy t=not-a-date user=alice"), registry_es
    )

    assert normalized.time is not None
    assert normalized.time.tzinfo is not None


# ---------------------------------------------------------------------------
# The registry write has to be readable by the next read
# ---------------------------------------------------------------------------


class NearRealTimeES:
    """
    Dict-backed fake that behaves like OpenSearch's refresh interval.

    An indexed document is invisible to `search` until a refresh happens, and
    a write only refreshes if it asked for one. Reproduced deliberately: this is
    exactly the state the API was in, and it is why a parser an operator had
    just registered disappeared from the page on reload.
    """

    def __init__(self, **indices):
        self.pending = {name: {} for name in indices}
        self.visible = {name: dict(docs) for name, docs in indices.items()}
        self.writes = []

    def _commit(self, index, doc_id, body, kwargs):
        self.writes.append({"op": "index", "index": index, "kwargs": dict(kwargs)})
        if kwargs.get("refresh"):
            self.visible.setdefault(index, {})[doc_id] = copy.deepcopy(body)
        else:
            self.pending.setdefault(index, {})[doc_id] = copy.deepcopy(body)

    def exists(self, index, id=None, **kwargs):
        if id is not None:
            return id in self.visible.get(index, {})
        return index in self.visible

    def get(self, index, id, **kwargs):
        return {"_source": copy.deepcopy(self.visible[index][id])}

    def search(self, index, body=None, **kwargs):
        docs = list(self.visible.get(index, {}).values())
        return {
            "hits": {"total": {"value": len(docs)}, "hits": [{"_source": d} for d in docs]}
        }

    def index(self, index, id=None, body=None, **kwargs):
        self._commit(index, id, body, kwargs)
        return {"_id": id}

    def update(self, index, id, body=None, retry_on_conflict=None, **kwargs):
        doc = dict(self.visible[index][id])
        if "doc" in body:
            doc.update(copy.deepcopy(body["doc"]))
        self._commit(index, id, doc, kwargs)
        return {"_id": id}

    def delete(self, index, id, **kwargs):
        self.writes.append({"op": "delete", "index": index, "kwargs": dict(kwargs)})
        if kwargs.get("refresh"):
            self.visible.get(index, {}).pop(id, None)
        else:
            self.pending.get(index, {}).pop(id, None)
        return {"result": "deleted", "_id": id}


@pytest.fixture
def nrt_routes(monkeypatch):
    from routes import parsers as parsers_routes

    fake = NearRealTimeES(**{PARSERS_INDEX: {}})
    monkeypatch.setattr(parsers_routes, "get_opensearch_client", lambda: fake)
    return parsers_routes, fake


def test_a_registered_parser_is_listed_immediately(nrt_routes):
    """
    POST then GET. The UI does nothing else between the two.
    """
    from schema.parser_record import ParserCreate

    routes, _fake = nrt_routes

    routes.create_parser(
        ParserCreate(parser_id="acme-proxy-v2", display_name="Acme proxy v2")
    )

    listed = [record["parser_id"] for record in routes.list_parsers()]
    assert "acme-proxy-v2" in listed, (
        "the write succeeded but the next read did not see it"
    )


def test_activating_a_parser_is_visible_to_the_next_read(nrt_routes):
    """
    The switch the UI flips when activating. If the status write were not
    readable, the toggle would appear to snap back and no parser could ever
    enter the chain.
    """
    from schema.parser_record import ParserCreate, ParserUpdate

    routes, _fake = nrt_routes
    routes.create_parser(
        ParserCreate(parser_id="acme-proxy-v2", display_name="Acme proxy v2")
    )

    routes.update_parser("acme-proxy-v2", ParserUpdate(status="active"))

    stored = {record["parser_id"]: record for record in routes.list_parsers()}
    assert stored["acme-proxy-v2"]["status"] == "active"


def test_a_deleted_parser_is_gone_immediately(nrt_routes):
    from schema.parser_record import ParserCreate

    routes, _fake = nrt_routes
    routes.create_parser(
        ParserCreate(parser_id="acme-proxy-v2", display_name="Acme proxy v2")
    )

    routes.delete_parser("acme-proxy-v2")

    listed = [record["parser_id"] for record in routes.list_parsers()]
    assert "acme-proxy-v2" not in listed


def test_every_registry_write_asks_for_a_refresh(nrt_routes):
    """
    The one invariant behind all three tests above: nothing in this module may
    write without `refresh`, or it is a write the operator cannot see.
    """
    from schema.parser_record import ParserCreate, ParserUpdate, ParserTestRequest

    routes, fake = nrt_routes
    routes.create_parser(
        ParserCreate(
            parser_id="acme-proxy-v2",
            display_name="Acme proxy v2",
            field_rules=[{"name": "user", "pattern": r"user=(\S+)"}],
        )
    )
    routes.update_parser("acme-proxy-v2", ParserUpdate(status="active"))
    routes.test_parser(
        ParserTestRequest(parser_id="acme-proxy-v2", sample="user=alice")
    )
    routes.delete_parser("acme-proxy-v2")

    assert fake.writes, "no writes were recorded"
    for write in fake.writes:
        assert write["kwargs"].get("refresh") == "wait_for", write
