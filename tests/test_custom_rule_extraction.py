"""
What a declarative field rule extracts, and what it admits to not extracting.

`extract_field_rules` had no tests at all, which is how a parser built through
the UI could extract `None` for every unquoted value while the UI's own sample
-- which quotes everything -- looked fine. The rules the UI generates are an
alternation with one group per branch, and reading "group 1" took the branch
that did not match.

These pin the contract the rest of the tier depends on: a rule that extracted
nothing must say so, and must not be able to claim a format on the strength of
an empty field.
"""

import copy

import pytest

from orchestrator import main as orchestrator_main
from parsers import custom_chain
from parsers.registry import extract_field_rules
from schema.raw_event import RawEventEnvelope
from tests.test_dlq_reprocessing import FakeES

PARSERS_INDEX = "ulpf-parsers"

# Exactly what ui/client/pages/CustomParsers.tsx generates for each key an
# operator types: accept the value quoted or bare, one branch per form.
def ui_rule(key):
    return {"name": key, "pattern": f'{key}="([^"]*)"|{key}=(\\S+)'}


UNQUOTED_LINE = (
    "time=2024-01-22T12:42:48Z level=error user=alice action=login src_ip=10.1.1.5"
)
QUOTED_LINE = (
    'time="2024-01-22T12:42:48Z" level="error" user="alice" action="login"'
)


@pytest.fixture(autouse=True)
def _clean_cache():
    custom_chain.clear_cache()
    yield
    custom_chain.clear_cache()


def build(rules, parser_id="acme-v1"):
    return custom_chain.build_parser({"parser_id": parser_id, "field_rules": rules})


def normalize(payload, rules):
    """Run the rules through the real pipeline tier, not just the helper."""
    es = FakeES(**{PARSERS_INDEX: {}})
    es.data[PARSERS_INDEX]["acme-v1"] = {
        "parser_id": "acme-v1",
        "status": "active",
        "priority": 10,
        "is_builtin": False,
        "version": 1,
        "field_rules": copy.deepcopy(rules),
    }
    custom_chain.clear_cache()
    event, _attempted = orchestrator_main.normalize_raw_event(
        RawEventEnvelope(
            event_id="raw-1",
            source_id="src-1",
            source_type="network_device",
            transport="other",
            collector_id="col-1",
            format_hint="acme",
            raw_payload=payload,
        ),
        es,
    )
    return event


# ---------------------------------------------------------------------------
# The alternation the UI generates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sample", [UNQUOTED_LINE, QUOTED_LINE], ids=["unquoted", "quoted"]
)
def test_the_rule_the_ui_generates_extracts_either_form(sample):
    """
    One group per alternation branch means only one group participates. The
    value is in whichever group that is, not necessarily group 1.
    """
    extracted, errors, misses = extract_field_rules(
        sample, [ui_rule("user"), ui_rule("action")]
    )

    assert extracted == {"user": "alice", "action": "login"}
    assert errors == []
    assert misses == []


def test_a_rule_that_extracted_nothing_is_named_not_hidden():
    extracted, _errors, misses = extract_field_rules(
        "level=error", [ui_rule("user"), ui_rule("action")]
    )

    assert extracted == {}
    assert misses == ["user", "action"]


def test_a_pattern_that_will_not_compile_is_an_error_not_a_miss():
    """A broken pattern and a pattern that found nothing are different faults."""
    extracted, errors, misses = extract_field_rules(
        UNQUOTED_LINE, [{"name": "user", "pattern": "user=("}, ui_rule("action")]
    )

    assert extracted == {"action": "login"}
    assert len(errors) == 1 and "invalid pattern" in errors[0]
    assert misses == []


# ---------------------------------------------------------------------------
# Value hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sample,rule,expected",
    [
        ("Accepted for user=alice, from 10.1.1.5,", r"user=(\S+)", "alice"),
        ("Accepted for user=alice; from 10.1.1.5;", r"user=(\S+)", "alice"),
        ("login failed for user=alice.", r"user=(\S+)", "alice"),
        ('user="alice smith"', 'user="([^"]*)"', "alice smith"),
        ("user='alice'", r"user=(\S+)", "alice"),
        ("user=  alice  ", r"user=(\s+\S+)", "alice"),
    ],
)
def test_quoting_and_line_punctuation_are_not_part_of_the_value(
    sample, rule, expected
):
    """\S+ stops at whitespace, not at the comma that ends the field."""
    extracted, _errors, _misses = extract_field_rules(
        sample, [{"name": "user", "pattern": rule}]
    )
    assert extracted == {"user": expected}


def test_a_capture_of_only_punctuation_is_a_miss():
    """Otherwise `src=10.1.1.5,` reaches src_endpoint and fails every check."""
    _extracted, _errors, misses = extract_field_rules(
        "src=..., dst=10.1.1.5",
        [{"name": "src", "pattern": r"src=(\S+)"}, {"name": "dst", "pattern": r"dst=(\S+)"}],
    )
    assert misses == ["src"]


def test_a_trailing_comma_does_not_cost_the_field_its_mapping():
    event = normalize(
        "acme-proxy t=2024-01-22T12:42:48Z user=alice, src_ip=10.1.1.5, action=login.",
        [
            {"name": "time", "pattern": r"t=(\S+)"},
            {"name": "user", "pattern": r"user=(\S+)"},
            {"name": "src_ip", "pattern": r"src_ip=(\S+)"},
            {"name": "action", "pattern": r"action=(\S+)"},
        ],
    )

    assert event is not None
    assert event.user == "alice"
    assert event.src_endpoint == "10.1.1.5"
    assert event.action == "login"
    assert event.time.year == 2024


# ---------------------------------------------------------------------------
# Case and anchors
# ---------------------------------------------------------------------------


def test_the_key_is_matched_case_insensitively():
    """A fleet sends User=, user= and USER= for the same field."""
    extracted, _errors, _misses = extract_field_rules(
        "User=alice USER=bob", [{"name": "user", "pattern": r"user=(\S+)"}]
    )
    assert extracted == {"user": "alice"}


def test_an_anchored_rule_anchors_to_the_line_not_the_string():
    """
    A payload or a pasted sample can be more than one line. Without
    MULTILINE, ^ matches only the very first line, so a rule written for the
    third line of a sample silently finds nothing.
    """
    payload = "2024-01-01 host a: user=alice\n2024-01-01 host b: user=bob"

    extracted, _errors, misses = extract_field_rules(
        payload, [{"name": "user", "pattern": r"^2024-01-01 host b: user=(\S+)"}]
    )
    assert extracted == {"user": "bob"}
    assert misses == []


# ---------------------------------------------------------------------------
# A rule set that extracted nothing must decline
# ---------------------------------------------------------------------------


def test_the_value_comes_from_the_group_that_took_part():
    """
    Not group 1, and not "the first group that exists": the alternation in
    the rule the UI generates puts the quoted value in group 1 and the bare
    value in group 2, and only one of the two is ever present.
    """
    extracted, _errors, _misses = extract_field_rules(
        "action=login", [{"name": "user", "pattern": r'user="([^"]*)"|action=(\S+)'}]
    )
    assert extracted == {"user": "login"}


def test_a_rule_whose_groups_all_missed_is_a_miss_not_a_field():
    """
    Reading group 1 blindly wrote None into the record. Every group optional
    and none of them matching means the rule found the line and no value in
    it, and an empty field is worse than a miss: it made a rule set that
    extracted nothing look like a match.
    """
    _extracted, _errors, misses = extract_field_rules(
        "action=login", [{"name": "user", "pattern": r"(?P<a>x)?(?P<b>y)?"}]
    )
    assert misses == ["user"]


def test_a_rule_set_that_extracted_nothing_declines_the_format():
    """
    The one answer a custom parser must not give is "yes, that is my format"
    on a line it read nothing from -- it would claim the traffic and hand the
    operator an event full of nulls.
    """
    foreign = "acme-proxy t=2024-01-22T12:42:48Z level=error"
    parser = build([{"name": "user", "pattern": r"user=(\S+)"}])
    assert parser(foreign, "raw-1", "src-1") is None
    assert normalize(foreign, [{"name": "user", "pattern": r"user=(\S+)"}]) is None


def test_one_working_rule_is_enough_to_claim_the_format():
    assert normalize(UNQUOTED_LINE, [ui_rule("user")]) is not None


# ---------------------------------------------------------------------------
# The full path, with the rules the UI actually writes
# ---------------------------------------------------------------------------


def test_a_parser_built_in_the_ui_normalizes_an_unquoted_log_line():
    event = normalize(
        UNQUOTED_LINE,
        [ui_rule("time"), ui_rule("level"), ui_rule("user"), ui_rule("action"), ui_rule("src_ip")],
    )

    assert event is not None
    assert event.time.year == 2024 and event.time.month == 1
    assert event.severity == "high"
    assert event.user == "alice"
    assert event.action == "login"
    assert event.src_endpoint == "10.1.1.5"
    assert event.parser_id == "acme-v1"
    assert event.parser_tier == "custom"
    assert event.confidence_score == pytest.approx(0.7)
