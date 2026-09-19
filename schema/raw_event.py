from datetime import datetime, timezone
from typing import Optional, Literal

from pydantic import BaseModel, Field


class RawEventEnvelope(BaseModel):
    event_id: str = Field(
        ...,
        description="Unique ID minted at ingestion time",
    )

    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    source_id: str = Field(...)

    source_type: Literal[
        "network_device",
        "server",
        "application",
        "database",
        "cloud",
        "iot",
        "custom",
    ] = Field(...)

    transport: Literal[
        "udp",
        "http",
        "file",
        "other",
    ] = Field(...)

    format_hint: Optional[str] = Field(
        None,
        description=(
            "Hint about log format: "
            "'syslog', 'json', 'cef', 'leef', "
            "'csv', 'xml', 'unknown'"
        ),
    )

    raw_payload: str = Field(...)

    bronze_uri: Optional[str] = Field(None)

    collector_id: str = Field(...)

    envelope_schema_version: str = Field("1.0.0")

    class Config:
        extra = "forbid"