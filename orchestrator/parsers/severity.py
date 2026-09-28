"""
Shared severity mapping for the Silver layer.

Numeric severities follow the syslog scale used by RFC5424, Elastic and Beats,
where a lower value is more severe: 0 emerg, 1 alert, 2 crit, 3 err,
4 warning, 5 notice, 6 info, 7 debug.

CEF declares its own 0-10 scale (higher is worse) and is mapped inside
cef_parser instead, so the two conventions are never mixed.
"""

from typing import Any, Optional

SYSLOG_SEVERITY_NAMES: dict[int, str] = {
    0: "emerg",
    1: "alert",
    2: "crit",
    3: "err",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}

SYSLOG_FACILITY_NAMES: dict[int, str] = {
    0: "kern",
    1: "user",
    2: "mail",
    3: "daemon",
    4: "auth",
    5: "syslog",
    6: "lpr",
    7: "news",
    8: "uucp",
    9: "cron",
    10: "authpriv",
    11: "ftp",
    12: "ntp",
    13: "security",
    14: "console",
    15: "solaris-cron",
    16: "local0",
    17: "local1",
    18: "local2",
    19: "local3",
    20: "local4",
    21: "local5",
    22: "local6",
    23: "local7",
}

SYSLOG_SEVERITY_TO_NORMALIZED: dict[int, str] = {
    0: "critical",
    1: "critical",
    2: "critical",
    3: "high",
    4: "medium",
    5: "low",
    6: "low",
    7: "low",
}

MAX_SYSLOG_PRI = 191

SEVEREITY_ORDER = ("low", "medium", "high", "critical")

SEVERITY_TEXT_LEVELS: dict[str, str] = {
    "emerg": "critical",
    "emergency": "critical",
    "panic": "critical",
    "fatal": "critical",
    "alert": "critical",
    "crit": "critical",
    "critical": "critical",
    "err": "high",
    "error": "high",
    "severe": "high",
    "failure": "high",
    "failed": "high",
    "high": "high",
    "warn": "medium",
    "warning": "medium",
    "medium": "medium",
    "notice": "low",
    "info": "low",
    "informational": "low",
    "low": "low",
    "debug": "low",
    "trace": "low",
}

# Checked most severe first so "fatal error" resolves to critical, not high.
SEVERITY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("emerg", "critical"),
    ("emergency", "critical"),
    ("panic", "critical"),
    ("fatal", "critical"),
    ("crit", "critical"),
    ("alert", "critical"),
    ("err", "high"),
    ("error", "high"),
    ("severe", "high"),
    ("fail", "high"),
    ("warn", "medium"),
)


def severity_from_text(value: Any) -> Optional[str]:
    """
    Map a severity token such as "critical", "WARN" or "err" onto
    low/medium/high/critical. Returns None for anything unrecognized.
    """

    if not isinstance(value, str):
        return None
    return SEVERITY_TEXT_LEVELS.get(value.strip().lower())


def severity_from_message(message: Any) -> Optional[str]:
    """
    Infer severity from free text by looking for severity keywords.

    The most severe keyword wins, so a message mentioning both "warning" and
    "fatal" is treated as critical. Returns None when no keyword is present.
    """

    if not isinstance(message, str):
        return None
    lowered = message.lower()
    for keyword, level in SEVERITY_KEYWORDS:
        if keyword in lowered:
            return level
    return None



def severity_from_number(value: Any) -> Optional[str]:
    """
    Map a numeric severity onto low/medium/high/critical.

    Returns None when the value is not an integer on the syslog 0-7 scale, so
    callers can fall back to their own text handling instead of guessing.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return SYSLOG_SEVERITY_TO_NORMALIZED.get(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.lstrip("+-").isdigit():
            return None
        parsed = int(stripped)
        return SYSLOG_SEVERITY_TO_NORMALIZED.get(parsed)
    return None


def severity_from_cef_number(value: Any) -> Optional[str]:
    """
    Map a CEF severity (0-10, higher is worse) onto low/medium/high/critical.

    Returns None when the value is not a number in range, so callers can fall
    back to text handling.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.lstrip("+-").isdigit():
            return None
        value = int(stripped)
    if not isinstance(value, int):
        return None
    if not 0 <= value <= 10:
        return None
    if value >= 8:
        return "critical"
    if value >= 5:
        return "high"
    if value >= 3:
        return "medium"
    return "low"


def syslog_severity_from_pri(pri: Any) -> Optional[str]:
    """
    Map a syslog <PRI> value onto low/medium/high/critical.

    PRI packs the facility in the high bits and the severity in the low three,
    so severity is pri % 8. Returns None for values outside 0-191.
    """

    if isinstance(pri, bool):
        return None
    if isinstance(pri, str):
        stripped = pri.strip()
        if not stripped or not stripped.isdigit():
            return None
        pri = int(stripped)
    if not isinstance(pri, int):
        return None
    if not 0 <= pri <= MAX_SYSLOG_PRI:
        return None
    return SYSLOG_SEVERITY_TO_NORMALIZED[pri % 8]


def syslog_facility_name(pri: Any) -> Optional[str]:
    """
    Return the syslog facility name for a <PRI> value, or None when unknown.
    """

    if isinstance(pri, bool):
        return None
    if isinstance(pri, str):
        stripped = pri.strip()
        if not stripped or not stripped.isdigit():
            return None
        pri = int(stripped)
    if not isinstance(pri, int):
        return None
    if not 0 <= pri <= MAX_SYSLOG_PRI:
        return None
    return SYSLOG_FACILITY_NAMES.get(pri // 8)


def syslog_severity_name(pri: Any) -> Optional[str]:
    """
    Return the syslog severity name (crit, err, ...) for a <PRI> value.
    """

    if isinstance(pri, bool):
        return None
    if isinstance(pri, str):
        stripped = pri.strip()
        if not stripped or not stripped.isdigit():
            return None
        pri = int(stripped)
    if not isinstance(pri, int):
        return None
    if not 0 <= pri <= MAX_SYSLOG_PRI:
        return None
    return SYSLOG_SEVERITY_NAMES[pri % 8]
