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


# Very simple BSD syslog parser for MVP (RFC 3164-like)
SYSLOG_PATTERN = re.compile(
    r"^(?:<(?P<pri>\d{1,3})>)?"
    r"(?P<timestamp>\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+"
    r"(?P<program>[^\[\]:]+?)(?:\[(?P<pid>\d+)\])?:\s+"
    r"(?P<message>.*)$"
)


def parse_syslog(raw_payload: str, raw_event_id: str, source_id: str) -> Optional[NormalizedEvent]:
    """
    Parse a BSD-style syslog line into a NormalizedEvent.
    Returns None if parsing fails (so fallback/generic parsers can try).
    """
    match = SYSLOG_PATTERN.match(raw_payload.strip())
    if not match:
        return None

    groups = match.groupdict()
    ts_str = groups["timestamp"]
    host = groups["host"]
    program = groups["program"]
    message = groups["message"]
    pri_raw = groups["pri"]
    pid_raw = groups["pid"]

    # Infer year from current year (MVP simplification)
    now = datetime.now(timezone.utc)
    try:
        # Parse without year, then attach current year
        ts_with_year = datetime.strptime(f"{now.year} {ts_str}", "%Y %b %d %H:%M:%S")
        ts_with_year = ts_with_year.replace(tzinfo=timezone.utc)
    except ValueError:
        # If parsing fails, let fallback handle it
        return None

    # An explicit <PRI> is the sender's own classification, so it wins.
    # Keyword inference is only the fallback for lines without a <PRI>.
    severity = syslog_severity_from_pri(pri_raw)
    if severity is None:
        severity = severity_from_message(message) or "low"

    extensions: Dict[str, Any] = {
        "source_id": source_id,
        "syslog_message": message,
    }
    if pri_raw is not None:
        extensions["syslog_pri"] = int(pri_raw)
        facility = syslog_facility_name(pri_raw)
        if facility is not None:
            extensions["syslog_facility"] = facility
        severity_name = syslog_severity_name(pri_raw)
        if severity_name is not None:
            extensions["syslog_severity_code"] = severity_name
    if pid_raw is not None:
        extensions["syslog_pid"] = int(pid_raw)

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
        time=ts_with_year,
        user=None,  # not extracted in this simple parser
        device_id=host,
        app_id=program,
        src_endpoint=None,
        dst_endpoint=None,
        protocol_name=None,
        action=None,
        extensions=extensions,
    )