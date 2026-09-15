from datetime import datetime, timezone
from typing import Optional, List, Literal, Dict, Any
from pydantic import BaseModel, Field


class DLQRecord(BaseModel):
    """
    Dead Letter Queue record for events that could not be normalized.
    """

    dlq_id: str = Field(..., description="Unique ID for this DLQ record")
    raw_event_id: str = Field(..., description="Reference to the raw event in Bronze")
    raw_payload: Optional[str] = Field(
        None, description="Optional copy of raw payload (can also be fetched from Bronze)"
    )
    parsers_attempted: List[str] = Field(
        ..., description="List of parser IDs that were tried on this event"
    )
    status: Literal["parse_failure", "schema_violation", "malformed", "unknown"] = Field(
        ..., description="Classification of why this event failed"
    )
    classification: Optional[str] = Field(
        None, description="Optional reason, e.g. 'no_timestamp', 'invalid_json', etc."
    )
    first_seen_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this event first entered DLQ",
    )
    last_attempt_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of last parse attempt",
    )
    reprocess_count: int = Field(0, ge=0, description="Number of times this event was reprocessed")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional debug info (errors, parser logs, etc.)",
    )

    class Config:
        extra = "forbid"