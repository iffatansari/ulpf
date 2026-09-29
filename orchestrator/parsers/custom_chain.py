"""
orchestrator/parsers/custom_chain.py

The tier that makes a registered custom parser actually run.

The registry (ulpf-parsers) is control over this chain. Before this module a
custom parser could be registered, listed, tested and deleted and the
pipeline would never call it, so "registered" and "working" were two
different things and nothing in the UI could tell you which one you had.

A custom parser is a list of named regex field rules -- data, never code,
because the registry is served over HTTP and stored in OpenSearch. The rules
are applied with the SAME function the /parsers/test endpoint uses
(parsers.registry.extract_field_rules), so what the test bench shows is what
the pipeline does. There is one rule engine, not two.

Where it runs: after the built-in chain has declined and before the Drain3
fallback.

  * After the built-ins, because a custom parser exists for a vendor format
    the chain does not understand; it must not outrank a real parser for a
    format that has one.
  * Before Drain3, because Drain3 would otherwise claim the same line and
    label it by template. A custom parser that never runs because a generic
    tier got there first changes nothing an operator can see.

Only `active` custom parsers are reached. A `draft` has not been put in the
chain yet and a `disabled` one has been taken out, and both mean the same
thing here: do not run it.

The loaded set is cached briefly (CUSTOM_PARSER_CACHE_SECONDS) so a burst of
events does not become a burst of registry reads. Registration in the UI is a
control-plane action, so a few seconds of propagation is the right trade --
but the cache is short enough that activating a parser is visible inside a
human's patience window, and a read is never allowed to break parsing: if the
registry cannot be read, the built-in chain still runs.
"""

import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from opensearchpy import OpenSearch

from schema.normalized_event import NormalizedEvent

from parsers.registry import extract_field_rules
from parsers.severity import severity_from_number, severity_from_text

# Same index and default the API resolves, so the writer and this reader agree
# without a second source of truth.
PARSERS_INDEX = os.getenv("PARSERS_INDEX", "ulpf-parsers")
OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://opensearch:9200")

# Seconds a loaded parser set stays usable. Registry changes are control-plane
# events; this bounds propagation without making every event read OpenSearch.
CACHE_TTL_SECONDS = float(os.getenv("CUSTOM_PARSER_CACHE_SECONDS", "15"))

# The tier name recorded on the normalized event and in parser lineage.
CUSTOM_TIER = "custom"

# Confidence policy: a custom parser is an operator's own declaration, not a
# vendor-validated decode, so it sits below every dedicated parser (0.90-0.95)
# and above the Drain3 guess (0.5). It ties with the "generic" tier.
CUSTOM_TIER_CONFIDENCE = 0.7

# Rule names carried onto dedicated schema fields. These deliberately reuse the
# vocabulary parsers/field_labeling.py already labels Drain3 variables with
# (src_ip, dst_ip, user, action), so the two heuristic tiers describe the same
# log line the same way instead of inventing a second set of names.
_CORE_FIELD_ALIASES: Dict[str, str] = {
    "user": "user",
    "username": "user",
    "account": "user",
    "src_user": "user",
    "device": "device_id",
    "device_id": "device_id",
    "host": "device_id",
    "hostname": "device_id",
    "src_host": "device_id",
    "app": "app_id",
    "app_id": "app_id",
    "application": "app_id",
    "app_name": "app_id",
    "service": "app_id",
    "src": "src_endpoint",
    "src_ip": "src_endpoint",
    "source_ip": "src_endpoint",
    "client_ip": "src_endpoint",
    "dst": "dst_endpoint",
    "dst_ip": "dst_endpoint",
    "dest": "dst_endpoint",
    "dest_ip": "dst_endpoint",
    "destination_ip": "dst_endpoint",
    "server_ip": "dst_endpoint",
    "protocol": "protocol_name",
    "proto": "protocol_name",
    "transport": "protocol_name",
    "action": "action",
    "verb": "action",
    "operation": "action",
}

# Rule names consumed into `time` and `severity` rather than extensions. The
# short forms are here because `t=` and `ts=` are what vendor lines actually
# call a timestamp, and a rule named after the key in the log is the obvious
# thing for an operator to write; a value that will not parse costs nothing
# (it falls back to ingestion time).
_TIME_KEYS = (
    "time",
    "timestamp",
    "@timestamp",
    "ts",
    "t",
    "date",
    "datetime",
    "event_time",
    "eventtime",
)
_SEVERITY_KEYS = ("severity", "level", "sev", "priority")

# Cached (monotonic timestamp, parsers). Module-level because the parser chain
# is one process-wide decision, like the Drain3 miner.
_CACHE: Dict[str, Any] = {"loaded_at": 0.0, "parsers": ()}

_CLIENT: Optional[OpenSearch] = None


def _client() -> OpenSearch:
    """
    A client for the registry, created once per process.

    The orchestrator's consumer loop already holds an OpenSearch client and
    passes it in; this exists for the second caller, the API's DLQ dry run,
    which loads this module's chain and has no client to hand over.
    """
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = OpenSearch(hosts=[OPENSEARCH_URL], timeout=10)
    return _CLIENT


def _parse_time(value: str) -> Optional[datetime]:
    """Parse an extracted timestamp, or None when it is not one we understand."""
    token = (value or "").strip()
    if not token:
        return None

    # Epoch seconds / milliseconds. A bare 10-digit number is a timestamp in
    # every log this pipeline sees; anything else is left to the parsers.
    if token.isdigit():
        number = int(token)
        if number > 10_000_000_000:  # milliseconds
            number //= 1000
        if 0 < number < 4_000_000_000:
            try:
                return datetime.fromtimestamp(number, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        return None

    # `fromisoformat` accepts `Z` from Python 3.11, but the repo also runs on
    # 3.10 in places, so normalise it first rather than depending on version.
    candidate = token[:-1] + "+00:00" if token.endswith("Z") else token
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _severity(value: str) -> Optional[str]:
    """Map an extracted severity token or number onto the four-level vocabulary."""
    return severity_from_number(value) or severity_from_text(value)


def _to_event(
    parser_id: str,
    extracted: Dict[str, str],
    raw_event_id: str,
    source_id: str,
) -> NormalizedEvent:
    """
    Turn a custom parser's extracted fields into a Silver event.

    Recognised names reach the dedicated schema fields; everything the rule set
    captured but the schema has no home for is preserved under `extensions`
    rather than dropped, so a custom parser can never silently lose a field it
    was asked to extract.
    """
    core: Dict[str, Any] = {}
    extensions: Dict[str, Any] = {}

    event_time: Optional[datetime] = None
    severity: Optional[str] = None

    for name, value in extracted.items():
        if name in _TIME_KEYS:
            event_time = _parse_time(value) or event_time
            continue
        if name in _SEVERITY_KEYS and severity is None:
            severity = _severity(value)
            continue
        field = _CORE_FIELD_ALIASES.get(name)
        if field is not None and field not in core:
            core[field] = value
            continue
        extensions[name] = value

    action = core.get("action")

    return NormalizedEvent(
        event_id=f"{raw_event_id}-norm",
        raw_event_id=raw_event_id,
        parser_id=parser_id,
        parser_tier=CUSTOM_TIER,
        confidence_score=CUSTOM_TIER_CONFIDENCE,
        # An operator's key/value extractor is an application-level parse, the
        # same shape json_parser-v1 produces.
        class_name="application_activity",
        category_name="application",
        activity_name=action or "custom_event",
        # Unknown severity is reported as "low" rather than guessed upward: a
        # custom rule set says what it says, and severity was not in it.
        severity=severity or "low",
        time=event_time or datetime.now(timezone.utc),
        user=core.get("user"),
        device_id=core.get("device_id"),
        app_id=core.get("app_id"),
        src_endpoint=core.get("src_endpoint"),
        dst_endpoint=core.get("dst_endpoint"),
        protocol_name=core.get("protocol_name"),
        action=action,
        extensions={
            **extensions,
            "source_id": source_id,
        },
    )


def build_parser(record: Dict[str, Any]) -> Optional[Callable[..., Optional[NormalizedEvent]]]:
    """
    Wrap one registry record as a parser callable.

    The callable is the same shape as every built-in parser --
    (raw_payload, raw_event_id, source_id) -> NormalizedEvent | None -- so the
    chain can try it without knowing it is declarative. Returning None when no
    rule matched is what tells the chain to keep going, and it is a real
    verdict: a rule set that extracts nothing is not this format.
    """
    parser_id = record.get("parser_id")
    rules = record.get("field_rules") or []
    if not parser_id or not rules:
        return None

    def parse_custom(
        raw_payload: str,
        raw_event_id: str,
        source_id: str,
    ) -> Optional[NormalizedEvent]:
        extracted, _errors, _misses = extract_field_rules(raw_payload, rules)
        if not extracted:
            return None
        return _to_event(parser_id, extracted, raw_event_id, source_id)

    return parse_custom


def _load(es: OpenSearch) -> List[Tuple[str, Callable[..., Optional[NormalizedEvent]]]]:
    """
    Read the active custom parsers, ordered as the chain should try them.

    Filtered in Python rather than by a query clause so that "active and not
    built in" has exactly one definition, in one place, and can be tested
    without an OpenSearch cluster. The registry holds a handful of documents.
    """
    try:
        response = es.search(
            index=PARSERS_INDEX,
            body={"size": 500, "query": {"match_all": {}}},
        )
    except Exception as exc:
        # An absent index is a normal state (the API has not seeded yet), and a
        # registry that cannot be read must never take parsing down with it.
        print(f"Custom parser registry unavailable: {exc}", flush=True)
        return []

    active: List[Dict[str, Any]] = []
    for hit in response.get("hits", {}).get("hits", []):
        record = hit.get("_source", {})
        if record.get("is_builtin"):
            continue
        # Only `active` is in the chain: draft has not been activated,
        # disabled has been taken out.
        if record.get("status") != "active":
            continue
        active.append(record)

    # priority ascending, then id, matching the order GET /parsers reports and
    # the order the operator arranged them in on the configurations page.
    active.sort(key=lambda record: (record.get("priority", 100), str(record.get("parser_id"))))

    parsers: List[Tuple[str, Callable[..., Optional[NormalizedEvent]]]] = []
    for record in active:
        callable_parser = build_parser(record)
        if callable_parser is not None:
            parsers.append((str(record.get("parser_id")), callable_parser))
    return parsers


def custom_parsers(
    es: Optional[OpenSearch] = None,
    force: bool = False,
) -> List[Tuple[str, Callable[..., Optional[NormalizedEvent]]]]:
    """
    The active custom parsers, cached for CACHE_TTL_SECONDS.
    """
    now = time.monotonic()
    if not force and (now - _CACHE["loaded_at"]) < CACHE_TTL_SECONDS:
        return list(_CACHE["parsers"])

    parsers = _load(es or _client())
    _CACHE["loaded_at"] = now
    _CACHE["parsers"] = tuple(parsers)
    return list(parsers)


def clear_cache() -> None:
    """
    Forget the loaded set and the client.

    Used by tests, and by any caller that has just changed the registry and
    cannot wait out the TTL.
    """
    global _CLIENT
    _CACHE["loaded_at"] = 0.0
    _CACHE["parsers"] = ()
    _CLIENT = None
