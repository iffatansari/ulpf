


from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field


class RawEventEnvelope(BaseModel):
    """
    Bronze-layer raw event envelope.
    Every log, regardless of format, is wrapped in this before being stored.
    """

    event_id: str = Field(..., description="Unique ID minted at ingestion time")
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the event was ingested",
    )
    source_id: str = Field(..., description="Logical ID of the log source (e.g., 'firewall-01')")
    source_type: Literal["network_device", "server", "application", "database", "cloud", "iot", "custom"] = Field(
        ..., description="High-level source category"
    )
    transport: Literal["syslog", "http_json", "file_tail", "other"] = Field(
        ..., description="Transport mechanism used to receive this log"
    )
    format_hint: Optional[str] = Field(
        None, description="Hint about log format: 'syslog', 'json', 'cef', 'leef', 'csv', 'xml', 'unknown'"
    )
    raw_payload: str = Field(..., description="Original raw log line or JSON blob as received")
    bronze_uri: Optional[str] = Field(
        None, description="URI/Key in MinIO where raw payload is stored (if stored separately)"
    )
    collector_id: str = Field(..., description="ID of the collector service that received this event")
    envelope_schema_version: str = Field(
        "1.0.0", description="Version of this envelope schema for forward compatibility"
    )

    class Config:
        extra = "forbid"
