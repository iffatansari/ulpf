from datetime import datetime, timezone
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field


class NormalizedEvent(BaseModel):
    """
    Silver-layer OCSF-aligned normalized event.
    This is the unified schema all parsers map into.
    """

    # Identity & traceability
    event_id: str = Field(..., description="Unique ID for the normalized event")
    raw_event_id: str = Field(..., description="Reference to the raw event in Bronze")
    parser_id: str = Field(..., description="Which parser produced this normalization")
    parser_tier: Literal["primary", "generic", "fallback", "drain3"] = Field(
        ..., description="Parser tier used (for confidence & lineage)"
    )
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence in this normalization (0–1)"
    )
    schema_id: str = Field("ulpc-ocsf-v1", description="Identifier for this schema profile")
    schema_version: str = Field("1.0.0", description="Version of this schema")

    # Taxonomy (OCSF-like)
    class_name: Optional[str] = Field(None, description="High-level class, e.g. 'network_activity'")
    category_name: Optional[str] = Field(None, description="Category, e.g. 'firewall', 'authentication'")
    activity_name: Optional[str] = Field(None, description="Specific activity, e.g. 'login_success'")
    severity: Optional[Literal["low", "medium", "high", "critical"]] = Field(
        None, description="Normalized severity"
    )

    # Core fields (strictly normalized)
    time: datetime = Field(..., description="Event timestamp in UTC (ISO 8601 / RFC 3339)")
    user: Optional[str] = Field(None, description="Normalized user/identity")
    device_id: Optional[str] = Field(None, description="Device/host identifier")
    app_id: Optional[str] = Field(None, description="Application/service identifier")

    # Network fields (when applicable)
    src_endpoint: Optional[str] = Field(None, description="Source IP or hostname")
    dst_endpoint: Optional[str] = Field(None, description="Destination IP or hostname")
    protocol_name: Optional[str] = Field(None, description="Protocol (TCP/UDP/HTTP/etc.)")
    action: Optional[str] = Field(None, description="Action taken (allow, deny, connect, etc.)")

    # Extensions for anything not in core schema
    extensions: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary additional fields from the source log",
    )

    class Config:
        extra = "forbid"