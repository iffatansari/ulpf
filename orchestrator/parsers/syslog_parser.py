import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from schema.normalized_event import NormalizedEvent
from parsers.severity import (
    severity_from_message,
    syslog_facility_name,
    syslog_severity_from_pri,
    syslog_severity_name,
)


# RFC 5424 fixed header:
#
#   <PRI>VERSION SP TIMESTAMP SP HOSTNAME SP APP-NAME SP PROCID SP MSGID
#   SP STRUCTURED-DATA [SP MSG]
#
# VERSION is pinned to 1 because that is the only version RFC 5424 defines; a
# different number means an unknown header shape, and guessing at it would put
# misaligned values into the event.
#
# The five middle fields are captured as \S+ and then run through _nil(), since
# "-" is the RFC's NILVALUE ("absent") rather than a real value. STRUCTURED-DATA
# and MSG are left as a raw remainder because both need quote-aware scanning
# that a single regex cannot do correctly.
RFC5424_PATTERN = re.compile(
    r"^(?:<(?P<pri>\d{1,3})>)?1"
    r"\s+(?P<timestamp>\S+)"
    r"\s+(?P<host>\S+)"
    r"\s+(?P<app>\S+)"
    r"\s+(?P<procid>\S+)"
    r"\s+(?P<msgid>\S+)"
    r"(?P<rest>.*)$"
)

# One PARAM-NAME="PARAM-VALUE" pair. The value may contain escaped quotes and
# backslashes, which is why this cannot be a naive [^"]* run.
_SD_PARAM_RE = re.compile(r'([^\s=\]]+)="((?:[^"\\]|\\.)*)"')

_SD_UNESCAPES = {"\\": "\\", '"': '"', "]": "]", "n": "\n", "r": "\r"}

# RFC 5424 section 6.4.1: a sender may prefix MSG with a UTF-8 BOM.
_BOM = "\ufeff"

# NILVALUE per RFC 5424 section 6.2.
_NILVALUE = "-"


def _nil(value: Optional[str]) -> Optional[str]:
    """
    Collapse RFC 5424's NILVALUE to None.

    Without this a missing hostname becomes the literal string "-", which
    then shows up as a real device id in OCSF.
    """
    if value is None or value == _NILVALUE:
        return None
    return value


def _unescape_sd_value(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append(_SD_UNESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_structured_data(text: str) -> tuple[Optional[Dict[str, Any]], str]:
    """
    Peel RFC 5424 STRUCTURED-DATA off the front of `text`.

    Returns (params, remaining_msg). `params` is None when the field is
    absent (NILVALUE) or malformed.

    Scans element by element rather than matching r"\\[.*?\\]" because a
    PARAM-VALUE is allowed to contain "]", and a non-greedy match would end
    the element at the first one, silently truncating the rest of the line.

    When two elements share a PARAM-NAME, the first wins. Flattening to a
    dict is lossy by nature; first-wins is at least deterministic.
    """
    remainder = text.lstrip()
    if not remainder.startswith("["):
        return None, remainder

    params: Dict[str, Any] = {}
    i = 0
    n = len(remainder)
    while i < n and remainder[i] == "[":
        j = i + 1
        in_quotes = False
        escaped = False
        while j < n:
            ch = remainder[j]
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quotes = not in_quotes
            elif ch == "]" and not in_quotes:
                break
            j += 1
        if j >= n:
            # Unterminated element: not valid STRUCTURED-DATA.
            return None, text
        element = remainder[i + 1 : j]
        i = j + 1

        sd_id, _, param_text = element.partition(" ")
        params.setdefault("sd_id", sd_id)
        for match in _SD_PARAM_RE.finditer(param_text):
            params.setdefault(match.group(1), _unescape_sd_value(match.group(2)))

    return params, remainder[i:].lstrip()


def _parse_rfc5424_timestamp(value: str) -> Optional[datetime]:
    """
    Parse an RFC 5424 TIMESTAMP into an aware UTC datetime.

    Returns None when unusable, which lets the caller fall back to the
    ingest time rather than inventing a timestamp. Fractional seconds are
    truncated to microseconds because fromisoformat on Python < 3.11 accepts
    neither a "Z" suffix nor more than 3 or 6 fractional digits.
    """
    if value == _NILVALUE:
        return None

    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    # Normalize an over-long or absent fractional part to 3 or 6 digits.
    match = re.match(
        r"^(?P<head>\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2})"
        r"(?P<frac>\.\d+)?"
        r"(?P<offset>[Zz]|[+-]\d{2}:?\d{2})?$",
        text,
    )
    if not match:
        return None

    frac = match.group("frac") or ""
    if frac:
        digits = frac[1:]
        if len(digits) <= 3:
            digits = digits.ljust(3, "0")
        elif len(digits) > 6:
            digits = digits[:6]
        else:
            digits = digits.ljust(6, "0")
        frac = "." + digits

    offset = match.group("offset")
    if offset is None or offset in ("Z", "z"):
        # RFC 5424 requires an offset, but senders do omit it. Assuming UTC
        # keeps the event rather than dropping it.
        offset = "+00:00"
    elif ":" not in offset:
        # Python < 3.11 rejects a bare ±HHMM offset once fractional seconds are
        # present, so always hand fromisoformat the ±HH:MM form.
        offset = f"{offset[:3]}:{offset[3:]}"

    try:
        parsed = datetime.fromisoformat(
            f"{match.group('head')}{frac}{offset}"
        )
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


# Very simple BSD syslog parser for MVP (RFC 3164-like)
SYSLOG_PATTERN = re.compile(
    r"^(?:<(?P<pri>\d{1,3})>)?"
    r"(?P<timestamp>\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+"
    r"(?P<program>[^\[\]:]+?)(?:\[(?P<pid>\d+)\])?:\s+"
    r"(?P<message>.*)$"
)


def _build_event(
    *,
    pri_raw: Optional[str],
    host: Optional[str],
    app: Optional[str],
    message: str,
    event_time: datetime,
    raw_event_id: str,
    source_id: str,
    extra_extensions: Optional[Dict[str, Any]] = None,
) -> NormalizedEvent:
    """
    Shared tail for both syslog dialects.

    Severity, PRI decomposition and the extension shape must not drift
    between RFC 3164 and RFC 5424, so both paths land here.
    """
    # An explicit <PRI> is the sender's own classification, so it wins.
    # Keyword inference is only the fallback for lines without a <PRI>.
    severity = syslog_severity_from_pri(pri_raw)
    if severity is None:
        severity = severity_from_message(message) or "low"

    extensions: Dict[str, Any] = {
        "source_id": source_id,
        "syslog_message": message,
    }
    if extra_extensions:
        extensions.update(extra_extensions)
    if pri_raw is not None:
        extensions["syslog_pri"] = int(pri_raw)
        facility = syslog_facility_name(pri_raw)
        if facility is not None:
            extensions["syslog_facility"] = facility
        severity_name = syslog_severity_name(pri_raw)
        if severity_name is not None:
            extensions["syslog_severity_code"] = severity_name

    return NormalizedEvent(
        event_id=f"{raw_event_id}-norm",
        raw_event_id=raw_event_id,
        parser_id="syslog-parser-v1",
        parser_tier="primary",
        confidence_score=0.9,
        schema_id="ulpc-ocsf-v1",
        schema_version="1.0.0",
        class_name="system_activity",
        category_name="os",
        activity_name="syslog_event",
        severity=severity,
        time=event_time,
        user=None,  # not extracted in this simple parser
        device_id=host,
        app_id=app,
        src_endpoint=None,
        dst_endpoint=None,
        protocol_name=None,
        action=None,
        extensions=extensions,
    )


def _parse_rfc5424(
    match: re.Match, raw_event_id: str, source_id: str, now: datetime
) -> Optional[NormalizedEvent]:
    groups = match.groupdict()
    pri_raw = groups["pri"]

    event_time = _parse_rfc5424_timestamp(groups["timestamp"]) or now

    # The header groups are joined by \s+, so `rest` still carries the space
    # that follows MSGID. Normalize before deciding what the field is.
    remainder = groups["rest"].lstrip()
    if remainder == _NILVALUE or remainder.startswith(_NILVALUE + " "):
        # STRUCTURED-DATA is NILVALUE; whatever follows (if anything) is MSG.
        # Matched as a whole token so a MSG that merely starts with "-" is
        # not mistaken for the placeholder.
        structured: Optional[Dict[str, Any]] = None
        message = remainder[1:].lstrip()
    else:
        structured, message = _split_structured_data(remainder)

    if message.startswith(_BOM):
        message = message[len(_BOM) :]

    extra: Dict[str, Any] = {}
    msgid = _nil(groups["msgid"])
    if msgid is not None:
        extra["syslog_msgid"] = msgid
    if structured:
        extra["syslog_structured_data"] = structured
    procid = _nil(groups["procid"])
    if procid is not None and procid.isdigit():
        extra["syslog_pid"] = int(procid)

    return _build_event(
        pri_raw=pri_raw,
        host=_nil(groups["host"]),
        app=_nil(groups["app"]),
        message=message,
        event_time=event_time,
        raw_event_id=raw_event_id,
        source_id=source_id,
        extra_extensions=extra or None,
    )


def _parse_rfc3164(
    match: re.Match, raw_event_id: str, source_id: str, now: datetime
) -> Optional[NormalizedEvent]:
    groups = match.groupdict()
    ts_str = groups["timestamp"]

    # Infer year from current year (MVP simplification)
    try:
        # Parse without year, then attach current year
        ts_with_year = datetime.strptime(f"{now.year} {ts_str}", "%Y %b %d %H:%M:%S")
        ts_with_year = ts_with_year.replace(tzinfo=timezone.utc)
    except ValueError:
        # If parsing fails, let fallback handle it
        return None

    extra: Dict[str, Any] = {}
    pid_raw = groups["pid"]
    if pid_raw is not None:
        extra["syslog_pid"] = int(pid_raw)

    return _build_event(
        pri_raw=groups["pri"],
        host=groups["host"],
        app=groups["program"],
        message=groups["message"],
        event_time=ts_with_year,
        raw_event_id=raw_event_id,
        source_id=source_id,
        extra_extensions=extra or None,
    )


def parse_syslog(raw_payload: str, raw_event_id: str, source_id: str) -> Optional[NormalizedEvent]:
    """
    Parse an RFC 5424 or BSD/RFC 3164 syslog line into a NormalizedEvent.

    Returns None if neither dialect matches, so the drain3 fallback and the
    remaining parsers still get their turn. RFC 5424 is tried first: its
    versioned header is unambiguous, whereas a 3164 line can never match it.
    """
    now = datetime.now(timezone.utc)
    payload = raw_payload.strip()
    if payload.startswith(_BOM):
        payload = payload.lstrip(_BOM)

    match = RFC5424_PATTERN.match(payload)
    if match is not None:
        return _parse_rfc5424(match, raw_event_id, source_id, now)

    match = SYSLOG_PATTERN.match(payload)
    if match is not None:
        return _parse_rfc3164(match, raw_event_id, source_id, now)

    return None