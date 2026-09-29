"""
orchestrator/parsers/registry.py

One authoritative description of the parser chain that actually runs.

The orchestrator owns the parsing. This module does not reimplement any of
it: each entry holds a reference to the real callable, so `run_parser` and the
pipeline execute the same function. A custom parser is declarative
field_rules, never uploaded code.

Nothing here is invented. If a parser does not exist, it is not listed --
ui/client/lib/parser-meta.ts used to advertise seven formats against a chain of
four, and the registry exists so that kind of drift has a single place to be
caught (see tests/test_parser_registry.py).
"""

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from schema.normalized_event import NormalizedEvent
from parsers.cef_parser import parse_cef_log
from parsers.drain_fallback import parse_drain
from parsers.json_parser import parse_json_log
from parsers.syslog_parser import parse_syslog


# Priority reflects the order normalize_raw_event tries the parsers in.
# Lower is tried earlier.
BUILTIN_PARSERS: Dict[str, Dict[str, Any]] = {
    "cef-parser-v1": {
        "display_name": "CEF",
        "fn": parse_cef_log,
        "tier": "primary",
        "priority": 10,
        "source_formats": ["cef"],
        "description": (
            "ArcSight CEF. Parses the CEF:Version|Vendor|Product|Version|"
            "Signature|Severity|Name header plus the key=value extension, "
            "with quoted-value support. Maps the header to metadata.log and "
            "the 0-10 CEF severity onto the four-level vocabulary."
        ),
        "sample_payload": (
            "CEF:0|Fortinet|FortiGate|7.2.0|100|allowed|5|"
            "src=10.0.0.5 dst=10.0.0.9 spt=54321 dpt=443 proto=tcp act=allow"
        ),
    },
    "json-parser-v1": {
        "display_name": "JSON Lines",
        "fn": parse_json_log,
        "tier": "primary",
        "priority": 20,
        "source_formats": ["json"],
        "description": (
            "One JSON object per line, coalescing balanced-brace objects "
            "that span several lines. Field names reach OCSF through an alias "
            "table; unmapped keys are preserved rather than dropped."
        ),
        "sample_payload": (
            '{"@timestamp":"2024-01-22T12:42:48Z",'
            '"src_ip":"10.0.0.9","msg":"connection reset"}'
        ),
    },
    "syslog-parser-v1": {
        "display_name": "Syslog",
        "fn": parse_syslog,
        "tier": "primary",
        "priority": 30,
        "source_formats": ["syslog"],
        "description": (
            "RFC 5424 and legacy RFC 3164. RFC 5424 gives an explicit "
            "timestamp and structured data; RFC 3164 has no year, so the "
            "current one is assumed. PRI severity wins over message "
            "keywords. NILVALUE fields are omitted rather than reported as "
            "a literal dash."
        ),
        "sample_payload": '<34>1 2024-01-22T12:42:48Z web1 sshd 2321 - - login failed',
    },
    "drain3-fallback-v1": {
        "display_name": "Drain3 fallback",
        "fn": parse_drain,
        "tier": "fallback",
        "priority": 40,
        "source_formats": ["text", "raw", "auto", "unknown", ""],
        "description": (
            "Last resort for anything the dedicated parsers decline. Mines "
            "the line into a Drain3 template and labels the wildcards into "
            "fields. This is template mining, not a fixed field list: it "
            "returns None when it cannot find a usable template, so prose is "
            "rejected rather than half-parsed."
        ),
        "sample_payload": "2024-01-22T12:42:48Z api-gateway login for user bob failed",
    },
}

BUILTIN_PARSER_IDS: Tuple[str, ...] = tuple(BUILTIN_PARSERS)


def get_builtin(parser_id: str) -> Optional[Dict[str, Any]]:
    spec = BUILTIN_PARSERS.get(parser_id)
    if spec is None:
        return None
    # Never hand the callable itself out to a caller that might serialize it.
    return {key: value for key, value in spec.items() if key != "fn"}


def run_parser(
    parser_id: str,
    raw_payload: str,
    raw_event_id: str,
    source_id: str,
) -> Optional[NormalizedEvent]:
    """
    Execute a built-in parser against a raw payload.

    Returns the NormalizedEvent, or None when the parser declines the line --
    the same contract the orchestrator's chain relies on. Raises KeyError for
    an id that is not a built-in, so a typo is never silently reported as
    "this parser did not match".
    """
    spec = BUILTIN_PARSERS.get(parser_id)
    if spec is None:
        raise KeyError(parser_id)
    fn: Callable = spec["fn"]
    return fn(raw_payload, raw_event_id, source_id)


# ---------------------------------------------------------------------------
# Declarative extraction for custom parsers
#
# A custom parser is a list of named regexes. It deliberately cannot hold
# code: the registry is served over HTTP and stored in OpenSearch, so
# executable user content would be remote code execution. These bounds keep a
# user-supplied pattern from becoming a denial-of-service via catastrophic
# backtracking on a large sample. Python's re has no timeout, so bounding the
# input and the pattern is the available lever.
# ---------------------------------------------------------------------------

MAX_SAMPLE_CHARS = 8_192
MAX_FIELD_RULES = 50
MAX_PATTERN_CHARS = 512
MAX_RULE_NAME_CHARS = 128


# Rules run case-insensitively and anchored per line. Neither is the author's
# problem to solve: a vendor fleet sends `User=`, `user=` and `USER=` for the
# same field, and ^ on a rule means "start of the log line", which is not the
# start of the string when the payload or the pasted sample carries more than
# one line. Without MULTILINE an anchored rule quietly matches the first line
# and extracts the wrong value, which is worse than not matching at all.
_RULE_FLAGS = re.IGNORECASE | re.MULTILINE

# Punctuation that belongs to the line rather than to the value. \S+ stops at
# whitespace, not at a comma, so `for alice, from 10.1.1.5,` extracts
# "alice," -- and "10.1.1.5," fails every value check downstream, so the
# address lands in extensions instead of src_endpoint.
_TRAILING_NOISE = ",;."


def _clean_value(value: str) -> str:
    """
    Strip the quoting and line punctuation a capture drags in with it.

    A quoted kv-pair carries its quotes only because the log format used
    them; they are not part of the field, and a quoted timestamp does not
    parse as a timestamp.
    """
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    return text.strip().rstrip(_TRAILING_NOISE).strip()


def _captured(match: "re.Match") -> str:
    """
    The first capture group that actually took part, else the whole match.

    Not "group 1". An alternation is the natural way to accept a field that
    is sometimes quoted and sometimes not, and it puts the two branches in
    different groups: for `k="([^"]*)"|k=(\\S+)` only one of the two
    participates, so reading group 1 yields None for every unquoted value.
    That is the rule the UI generates for every key an operator types, and
    it meant a parser built in the browser extracted nothing from a plain
    `level=error` while the same parser worked on a quoted sample.
    """
    for group in match.groups():
        if group is not None:
            return group
    return match.group(0)


def extract_field_rules(
    sample: str, field_rules: List[Dict[str, Any]]
) -> Tuple[Dict[str, str], List[str], List[str]]:
    """
    Run declarative field rules over a sample line.

    Returns (extracted, errors, misses). A rule whose pattern will not
    compile is reported in `errors` and skipped, so one bad rule does not
    void the rest. A rule that matched nothing usable is named in `misses`:
    a rule that silently extracts nothing is indistinguishable from a
    working one, and "which of my five rules is wrong" is the whole
    question when extraction looks broken.

    A rule that matched but yielded no value -- an optional group that did
    not participate, or a capture that was only punctuation -- is a miss
    too. Writing it as an empty field would let a rule set that extracted
    nothing claim the format, which is the one answer a custom parser must
    never give.
    """
    extracted: Dict[str, str] = {}
    errors: List[str] = []
    misses: List[str] = []
    haystack = sample[:MAX_SAMPLE_CHARS]

    for rule in list(field_rules or [])[:MAX_FIELD_RULES]:
        name = (rule or {}).get("name")
        pattern = (rule or {}).get("pattern")
        if not name or not pattern:
            errors.append("rule needs both a name and a pattern")
            continue
        if len(str(name)) > MAX_RULE_NAME_CHARS:
            errors.append(f"field name too long: {name[:32]}...")
            continue
        if len(str(pattern)) > MAX_PATTERN_CHARS:
            errors.append(f"pattern too long for rule {name}")
            continue
        try:
            compiled = re.compile(str(pattern), _RULE_FLAGS)
        except re.error as exc:
            errors.append(f"rule {name}: invalid pattern ({exc})")
            continue
        try:
            match = compiled.search(haystack)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"rule {name}: match failed ({exc})")
            continue
        if match is None:
            misses.append(str(name))
            continue
        value = _clean_value(_captured(match))
        if not value:
            misses.append(str(name))
            continue
        extracted[str(name)] = value

    return extracted, errors, misses
