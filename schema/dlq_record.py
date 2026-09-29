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
    status: Literal[
    "parse_failure",
    "schema_violation",
    "malformed",
    "index_failure",
    "unknown",
] = Field(
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

    # -------------------------------------------------------------
    # Module 3: recovery audit.
    #
    # `status` above is the ORIGINAL failure classification and is
    # never overwritten -- "was this event originally a failure?" must
    # stay answerable after a successful replay. Recovery is tracked in
    # a separate axis so history is preserved.
    # -------------------------------------------------------------
    resolution_status: Literal["unresolved", "recovered"] = Field(
        "unresolved",
        description="Whether a later reprocess attempt has succeeded",
    )
    resolved_at: Optional[datetime] = Field(
        None, description="UTC timestamp of the successful reprocess, if recovered"
    )
    last_reprocess_id: Optional[str] = Field(
        None, description="ReprocessRun id of the most recent replay attempt"
    )
    replay_reason: Optional[str] = Field(
        None, description="Why the operator asked for a replay (audit)"
    )
    attempt_history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Per-attempt audit trail: attempt, reprocess_id, started_at, "
            "result, reason. Appended, never rewritten."
        ),
    )

    class Config:
        extra = "forbid"