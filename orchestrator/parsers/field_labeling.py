"""
Drain3 variable field labeling.

Drain3 identifies variable positions in a log template, but it does not
know what those variables mean. This module maps recognizable values and
their nearby context into normalized field names.

Priority:
1. Recognize the value itself (IP, MAC, action word, port).
2. Use nearby template context (user, port).
3. Preserve anything unknown as unlabeled instead of dropping it.
"""

import re
from dataclasses import dataclass, field


IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

ALLOW_WORDS = {
    "accepted",
    "accept",
    "allow",
    "allowed",
    "permit",
    "permitted",
}

DENY_WORDS = {
    "denied",
    "deny",
    "block",
    "blocked",
    "reject",
    "rejected",
    "drop",
    "dropped",
}

VARIABLE_TOKENS = {
    "<*>",
    "<IP>",
    "<USER>",
    "<ACTION>",
    "<PORT>",
}


@dataclass
class LabelResult:
    labeled: dict[str, str] = field(default_factory=dict)
    unlabeled: dict[str, str] = field(default_factory=dict)


def _has_variable_placeholder(token: str) -> bool:
    return any(variable in token for variable in VARIABLE_TOKENS)


def _context_words(tokens: list[str], var_index: int) -> tuple[str, str]:
    """
    Return the nearest meaningful token before and after a variable.

    Drain3 placeholders are skipped.
    """
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


def _store_port(result: LabelResult, value: str) -> bool:
    """
    Recognize either:
      443
      port=443

    Return True when the value is a valid TCP/UDP port.
    """
    candidate = value.strip()

    if candidate.lower().startswith("port="):
        candidate = candidate.split("=", 1)[1].strip()

    if candidate.isdigit() and 0 < int(candidate) <= 65535:
        result.labeled["port"] = candidate
        return True

    return False


def label_variables(template: str, variables: list[str]) -> LabelResult:
    tokens = template.split()

    var_positions = [
        i
        for i, token in enumerate(tokens)
        if token in VARIABLE_TOKENS or _has_variable_placeholder(token)
    ]

    result = LabelResult()

    # The first two confidently recognized IPs are treated as
    # source and destination respectively.
    claimed_ip_slots = ["src_ip", "dst_ip"]

    # Action words can become literal template tokens at a higher
    # similarity threshold, so inspect fixed template tokens too.
    for token in tokens:
        token_lower = token.strip(":,.").lower()

        if token_lower in ALLOW_WORDS:
            result.labeled["action"] = "allow"

        elif token_lower in DENY_WORDS:
            result.labeled["action"] = "deny"

    for idx, value in enumerate(variables):
        if idx >= len(var_positions):
            result.unlabeled[f"var_{idx}"] = value
            continue

        position = var_positions[idx]
        before, after = _context_words(tokens, position)
        value_clean = value.strip()
        value_lower = value_clean.lower()

        # 1. Value-based recognition.
        if value_lower in ALLOW_WORDS:
            result.labeled["action"] = "allow"
            continue

        if value_lower in DENY_WORDS:
            result.labeled["action"] = "deny"
            continue

        if MAC_RE.match(value_clean):
            result.labeled.setdefault("mac", value_clean)
            continue

        if IPV4_RE.match(value_clean):
            slot = claimed_ip_slots.pop(0) if claimed_ip_slots else None

            if slot:
                result.labeled[slot] = value_clean
            else:
                result.unlabeled[f"ip_{idx}"] = value_clean

            continue

        # Handles both <PORT> -> "22" and a Drain3 wildcard that
        # captures the complete token "port=22".
        if _store_port(result, value_clean):
            continue

        # 2. Context-based recognition.
        if before == "user" or after == "user":
            result.labeled["user"] = value_clean
            continue

        if before == "port" and _store_port(result, value_clean):
            continue

        # 3. Preserve unknown values.
        result.unlabeled[f"var_{idx}"] = value_clean

    return result
