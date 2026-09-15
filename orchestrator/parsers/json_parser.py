import json
from datetime import datetime, timezone
from typing import Optional, Any, Dict

from schema.normalized_event import NormalizedEvent


def parse_json_log(raw_payload: str, raw_event_id: str, source_id: str) -> Optional[NormalizedEvent]:
    """
    Parse a JSON log line into a NormalizedEvent.
    Expects something like:
    {
      "time": "2026-09-14T10:12:34Z",
      "level": "INFO",
      "user": "alice",
      "action": "login_success",
      "src_ip": "10.0.0.5",
      "message": "User logged in"
    }
    """
    try:
        data = json.loads(raw_payload.strip())
    except json.JSONDecodeError:
        return None

    # Extract time (try common keys)
    time_val = data.get("time") or data.get("timestamp") or data.get("@timestamp")
    if not time_val:
        return None

    try:
        ts = datetime.fromisoformat(time_val.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None

    # Map to OCSF-like fields
    user = data.get("user") or data.get("username")
    action = data.get("action") or data.get("event") or data.get("activity")
    src_ip = data.get("src_ip") or data.get("source_ip") or data.get("client_ip")
    severity_raw = data.get("level") or data.get("severity") or "INFO"

    severity = "low"
    if isinstance(severity_raw, str):
        s = severity_raw.lower()
        if s in ("error", "err", "critical", "crit"):
            severity = "high"
        elif s in ("warn", "warning"):
            severity = "medium"

    return NormalizedEvent(
        event_id=f"{raw_event_id}-norm",
        raw_event_id=raw_event_id,
        parser_id="json-parser-v1",
        parser_tier="primary",
        confidence_score=0.95,
        schema_id="ulpc-ocsf-v1",
        schema_version="1.0.0",
        class_name="application_activity",
        category_name="application",
        activity_name=action or "app_event",
        severity=severity,
        time=ts,
        user=user,
        device_id=data.get("host") or data.get("device_id"),
        app_id=data.get("app") or data.get("service"),
        src_endpoint=src_ip,
        dst_endpoint=data.get("dst_ip") or data.get("destination_ip"),
        protocol_name=data.get("protocol"),
        action=action,
        extensions={
            "source_id": source_id,
            "original_json": data,
        },
    )