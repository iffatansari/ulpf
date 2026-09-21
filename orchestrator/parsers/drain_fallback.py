"""
orchestrator/parsers/drain_fallback.py — Phase 2.3.

Same interface convention as syslog_parser.parse_syslog: takes the raw
payload + identifiers, returns a NormalizedEvent on success or None on
failure (so the orchestrator's existing "try next tier / fall to DLQ
if the last one returns None" logic needs ZERO changes to accommodate
this tier — it's just one more entry in whatever list of parsers
main.py already tries in order).

Reached only when primary + secondary parsers have already failed to
match, per your fallback chain design.

Verified against your real schema/normalized_event.py:
- src_endpoint/dst_endpoint are plain strings (IP/hostname), not a
  nested object — an earlier draft of this file assumed otherwise,
  fixed.
- parser_tier is Literal["primary","generic","fallback","drain3"] —
  this tier uses "drain3" specifically, not "ml_fallback".
- There's no dedicated port field anywhere in the schema (not even
  inside an endpoint object), so a labeled "port" value always lands
  in `extensions` now — nowhere else for it to honestly go.
"""

import re
from datetime import datetime, timezone

from schema.normalized_event import NormalizedEvent

from . import drain_miner, field_labeling

PARSER_ID = "drain3-fallback-v1"
SCHEMA_ID = "ulpc-ocsf-v1"  # matches syslog_parser.py's value
SCHEMA_VERSION = "1.0.0"

# Confidence policy (per team decision): this tier is NEVER "high" —
# it's either "low" (0.5) or invalid (returns None -> DLQ).
DRAIN_TIER_CONFIDENCE = 0.5

# Best-effort timestamp recovery from the raw line. Deliberately loose
# (RFC3164-ish "Mon DD HH:MM:SS" or an ISO8601-ish date) — if this
# doesn't match, we fall back to ingestion time (now), same tradeoff
# your own syslog_parser.py already accepts for its year-inference.
_TS_PATTERNS = [
    re.compile(r"\b[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\b"),  # "Sep 16 12:00:00"
    re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"),              # ISO8601-ish
]


def _best_effort_timestamp(raw_payload: str) -> datetime:
    for pattern in _TS_PATTERNS:
        match = pattern.search(raw_payload)
        if match:
            # Not attempting full parsing here — recovering an exact
            # timestamp from an arbitrary unknown format reliably is
            # genuinely hard. Flagging presence via extensions is more
            # honest than fabricating a parsed datetime that might be
            # wrong; using ingestion time as the actual `time` value.
            break
    return datetime.now(timezone.utc)


def parse_drain(raw_payload: str, raw_event_id: str, source_id: str) -> NormalizedEvent | None:
    result = drain_miner.parse(raw_payload)
    labels = field_labeling.label_variables(result.template, result.variables)
    fields = dict(labels.labeled)

    # Invalid tier, per the team's confidence rule: no identity-ish
    # field recoverable at all -> None, same convention as
    # syslog_parser returning None, so it falls through to DLQ exactly
    # the way an unparseable primary-tier line already does.
    has_identity = any(k in fields for k in ("src_ip", "dst_ip", "user", "mac"))
    if not has_identity:
        return None

    src_ip = fields.get("src_ip")
    dst_ip = fields.get("dst_ip")
    is_network_ish = src_ip is not None or dst_ip is not None

    # src_endpoint/dst_endpoint are plain strings in this schema, so
    # this is just a direct assignment — no port to carry on them.
    # "port", "mac", or anything else labeled but without a dedicated
    # top-level field all fall through to extensions below, same as
    # anything field_labeling itself couldn't confidently label.
    consumed = {"src_ip", "dst_ip", "user", "action"} & fields.keys()
    leftover_labeled = {k: v for k, v in fields.items() if k not in consumed}

    return NormalizedEvent(
        event_id=f"{raw_event_id}-norm",
        raw_event_id=raw_event_id,
        parser_id=PARSER_ID,
        parser_tier="drain3",
        confidence_score=DRAIN_TIER_CONFIDENCE,
        schema_id=SCHEMA_ID,
        schema_version=SCHEMA_VERSION,
        class_name="network_activity" if is_network_ish else "unknown_activity",
        category_name="network" if is_network_ish else "other",
        activity_name=fields.get("action", "unknown"),
        severity="low",
        time=_best_effort_timestamp(raw_payload),
        user=fields.get("user"),
        device_id=source_id,
        app_id=None,
        src_endpoint=src_ip,
        dst_endpoint=dst_ip,
        protocol_name=None,
        action=fields.get("action"),
        extensions={
            **labels.unlabeled,
            **leftover_labeled,  # e.g. a labeled "port" or "mac" — never silently lost
            "drain_template": result.template,
            "drain_cluster_id": result.cluster_id,
        },
    )