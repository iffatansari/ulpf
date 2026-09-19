from datetime import datetime, timezone
from typing import Optional

from schema.normalized_event import NormalizedEvent


def parse_cef_log(
    raw_payload: str,
    raw_event_id: str,
    source_id: str,
) -> Optional[NormalizedEvent]:
    """
    Parse a basic CEF (Common Event Format) event.

    Expected structure:

    CEF:Version|Device Vendor|Device Product|Device Version|
    Signature ID|Name|Severity|Extension
    """

    raw_payload = raw_payload.strip()

    if not raw_payload.startswith("CEF:"):
        return None

    try:
        header_and_extension = raw_payload.split("|", 7)

        if len(header_and_extension) != 8:
            return None

        cef_version = header_and_extension[0]
        device_vendor = header_and_extension[1]
        device_product = header_and_extension[2]
        device_version = header_and_extension[3]
        signature_id = header_and_extension[4]
        name = header_and_extension[5]
        severity_raw = header_and_extension[6]
        extension_raw = header_and_extension[7]

        if not cef_version.startswith("CEF:"):
            return None

        # Convert CEF severity into our normalized severity.
        try:
            severity_number = int(severity_raw)

            if severity_number >= 8:
                severity = "critical"
            elif severity_number >= 5:
                severity = "high"
            elif severity_number >= 3:
                severity = "medium"
            else:
                severity = "low"

        except ValueError:
            severity = "medium"

        # Parse CEF extension key=value pairs.
        extensions = {}

        for item in extension_raw.split():
            if "=" not in item:
                continue

            key, value = item.split("=", 1)
            extensions[key] = value

        # Common CEF fields.
        src_ip = extensions.get("src")
        dst_ip = extensions.get("dst")
        user = (
            extensions.get("suser")
        or extensions.get("duser")
        or extensions.get("user")
                )
        device_id = extensions.get("dvchost") or extensions.get("dhost")
        protocol = extensions.get("proto")
        action = extensions.get("act")

        # CEF can contain an event timestamp.
        time_value = extensions.get("rt") or extensions.get("end")

        if time_value:
            try:
                # Common CEF timestamp form:
                # milliseconds since Unix epoch.
                timestamp_ms = int(time_value)
                event_time = datetime.fromtimestamp(
                    timestamp_ms / 1000,
                    tz=timezone.utc,
                )
            except (ValueError, TypeError, OverflowError):
                event_time = datetime.now(timezone.utc)
        else:
            event_time = datetime.now(timezone.utc)

        return NormalizedEvent(
            event_id=f"{raw_event_id}-norm",
            raw_event_id=raw_event_id,
            parser_id="cef-parser-v1",
            parser_tier="primary",
            confidence_score=0.90,
            schema_id="ulpc-ocsf-v1",
            schema_version="1.0.0",
            class_name="security_activity",
            category_name="security",
            activity_name=name or "cef_event",
            severity=severity,
            time=event_time,
            user=user,
            device_id=device_id or device_product,
            app_id=device_product,
            src_endpoint=src_ip,
            dst_endpoint=dst_ip,
            protocol_name=protocol,
            action=action,
            extensions={
                "source_id": source_id,
                "cef_version": cef_version,
                "device_vendor": device_vendor,
                "device_product": device_product,
                "device_version": device_version,
                "signature_id": signature_id,
                "signature_name": name,
                "cef_severity": severity_raw,
                "cef_extension": extensions,
            },
        )

    except (ValueError, IndexError):
        return None