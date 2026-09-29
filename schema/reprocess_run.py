from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


ReprocessStatus = Literal["queued", "running", "completed", "partial", "failed"]


class ReprocessRun(BaseModel):
    """
    One user-initiated recovery attempt over one or more DLQ records.

    A run is created when the API accepts a replay request, before any
    event is republished, so the UI always has an id to poll. The
    orchestrator then advances `recovered_count` / `failed_count` as each
    replayed event resolves, which is what lets a 1000-event batch report
    partial progress instead of blocking the request.
    """

    reprocess_id: str = Field(..., description="Unique ID for this reprocess run")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the run was accepted",
    )
    started_at: Optional[datetime] = Field(
        None, description="UTC timestamp of the first published replay"
    )
    completed_at: Optional[datetime] = Field(
        None, description="UTC timestamp of the last replay to resolve"
    )
    status: ReprocessStatus = Field(
        "queued", description="Lifecycle state of the run"
    )

    requested_count: int = Field(
        0, ge=0, description="How many DLQ records the request asked for"
    )
    published_count: int = Field(
        0, ge=0, description="How many raw events were republished to Redpanda"
    )
    recovered_count: int = Field(
        0, ge=0, description="How many replays reached Silver"
    )
    failed_count: int = Field(
        0, ge=0, description="How many replays failed again and stayed in DLQ"
    )

    event_ids: List[str] = Field(
        default_factory=list, description="Bronze event_ids that were replayed"
    )
    dlq_ids: List[str] = Field(
        default_factory=list, description="DLQ records this run covers"
    )
    reason: Optional[str] = Field(
        None, description="Operator-supplied reason for the replay"
    )
    # Events that could not even be published (missing Bronze document, etc.)
    # are surfaced separately so a bad replay is never silently counted as a
    # successful publish.
    errors: List[dict] = Field(
        default_factory=list,
        description="Per-DLQ publication errors: dlq_id, detail",
    )

    class Config:
        extra = "forbid"
