"""
orchestrator/parsers/field_labeling.py — Phase 2.3.

Drain3 tells you WHERE the variable parts of a template are, never
WHAT they mean. This module guesses the meaning, using two signals in
priority order:

  1. The variable's own VALUE shape/content (an IP-shaped string, a
     port-range number, a known allow/deny word). Checked FIRST and
     wins regardless of context, because — verified while building
     this — Drain3 can literally swallow a meaningful word like
     "denied" into a wildcard whose surrounding template context is
     identical to a neighboring wildcard's. If you only trusted
     position/context, that case mislabels. Value content is what
     rescues it.
  2. The template TEXT immediately around the <*> placeholder (the
     nearest real word before/after it — skipping past any adjacent
     <*> tokens, which carry no context of their own).

Anything that doesn't confidently match either signal is NOT dropped —
it goes into `unlabeled`, which the mapper should put straight into
NormalizedEvent.extensions. Losing data silently is worse than an
honestly-unlabeled field.
"""

import re
from dataclasses import dataclass, field

IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

ALLOW_WORDS = {"accepted", "accept", "allow", "allowed", "permit", "permitted"}
DENY_WORDS = {"denied", "deny", "block", "blocked", "reject", "rejected", "drop", "dropped"}


@dataclass
class LabelResult:
    labeled: dict[str, str] = field(default_factory=dict)
    unlabeled: dict[str, str] = field(default_factory=dict)


VARIABLE_TOKENS = {"<*>", "<IP>", "<USER>", "<ACTION>"}


def _has_variable_placeholder(token: str) -> bool:
    return any(variable in token for variable in VARIABLE_TOKENS)


def _context_words(tokens: list[str], var_index: int) -> tuple:
    """Nearest real word before and after position var_index, skipping all masked placeholders."""
    current_token = tokens[var_index]
    if "=" in current_token:
        key = current_token.split("=", 1)[0].strip(":,.").lower()
        if key:
            return key, key

    before = ""
    for i in range(var_index - 1, -1, -1):
        if not _has_variable_placeholder(tokens[i]):
            before = tokens[i].strip(":,.").lower()
            break
    after = ""
    for i in range(var_index + 1, len(tokens)):
        if not _has_variable_placeholder(tokens[i]):
            after = tokens[i].strip(":,.").lower()
            break
    return before, after


def label_variables(template: str, variables: list[str]) -> LabelResult:
    tokens = template.split()
    var_positions = [
        i for i, t in enumerate(tokens) if t in VARIABLE_TOKENS or _has_variable_placeholder(t)
    ]

    result = LabelResult()
    claimed_ip_slots = ["src_ip", "dst_ip"]  # first IP found -> src, second -> dst

    # Action words can end up as LITERAL template text instead of an
    # extracted variable (verified: happens with a higher sim_th,
    # since the word then differs enough from other lines to stay its
    # own template rather than generalizing into <*>). Check the
    # template's fixed tokens for this before falling through.
    for tok in tokens:
        tok_l = tok.strip(":,.").lower()
        if tok_l in ALLOW_WORDS:
            result.labeled["action"] = "allow"
        elif tok_l in DENY_WORDS:
            result.labeled["action"] = "deny"

    for idx, value in enumerate(variables):
        if idx >= len(var_positions):
            # more extracted values than <*> tokens shouldn't happen,
            # but don't crash the pipeline over a mismatch — bucket it
            result.unlabeled[f"var_{idx}"] = value
            continue

        before, after = _context_words(tokens, var_positions[idx])
        value_lower = value.strip().lower()

        # 1) value content first
        if value_lower in ALLOW_WORDS:
            result.labeled["action"] = "allow"
            continue
        if value_lower in DENY_WORDS:
            result.labeled["action"] = "deny"
            continue
        if MAC_RE.match(value):
            result.labeled.setdefault("mac", value)
            continue
        if IPV4_RE.match(value):
            slot = claimed_ip_slots.pop(0) if claimed_ip_slots else None
            if slot:
                result.labeled[slot] = value
            else:
                result.unlabeled[f"ip_{idx}"] = value
            continue

        # 2) context word second
        if before == "user" or after == "user":
            result.labeled["user"] = value
            continue
        if before == "port" and value.isdigit() and 0 < int(value) <= 65535:
            result.labeled["port"] = value
            continue

        # 3) nothing confident — keep it, just unlabeled
        result.unlabeled[f"var_{idx}"] = value

    return result