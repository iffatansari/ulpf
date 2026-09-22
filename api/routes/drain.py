"""
api/routes/drain.py — HTTP bridge the UI's Drain3 page uses to reach the
orchestrator's drain files.

The streaming orchestrator (orchestrator/parsers/drain_miner.py) is the
single owner of the Drain3 template-mining logic. Instead of duplicating a
"clone" in the browser, the UI page pastes raw log lines here and receives
the mined templates grouped by cluster, so there is exactly one
implementation across the pipeline and the dashboard demo.
"""

import re
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Reuse the exact module the orchestrator process imports. It lives at
# /app/orchestrator in the container layout (PYTHONPATH=/app) and at
# <repo-root>/orchestrator in a local checkout, so resolve it relative to
# this file rather than relying on cwd.
_ORCHESTRATOR = Path(__file__).resolve().parents[2] / "orchestrator"
if str(_ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR))

from parsers import drain_miner  # noqa: E402

router = APIRouter()

# Noise lines skipped before mining — mirrors the pre-filter the Drain3
# page describes (separators / bare counters are not log templates).
_NOISE_RE = re.compile(r"^={3,}|^-{3,}|^\d+$")

MAX_LINES = 20_000


class DrainClusterRequest(BaseModel):
    content: str


@router.post("/drain/cluster")
def cluster_logs(req: DrainClusterRequest) -> dict:
    """
    Run a pasted log stream through the orchestrator's Drain3 miner and
    return the mined templates grouped by cluster, ordered by frequency.
    """
    lines = [ln.strip() for ln in req.content.splitlines()]
    lines = [ln for ln in lines if ln and not _NOISE_RE.match(ln)]

    if not lines:
        return {"clusters": []}
    if len(lines) > MAX_LINES:
        raise HTTPException(
            status_code=413,
            detail=f"stream too large (limit {MAX_LINES} lines)",
        )

    mined = drain_miner.cluster_lines(lines)

    # Drain's template_mined for a message reflects the cluster's template
    # as of THAT message — earlier lines keep the literal until a later
    # line merges and wildcards it. Group by cluster_id and let the last
    # template seen be the cluster's canonical one.
    buckets: dict[int, dict] = {}
    for (cluster_id, template), line in zip(mined, lines):
        bucket = buckets.setdefault(cluster_id, {"template": template, "lines": []})
        bucket["template"] = template
        bucket["lines"].append(line)

    clusters = [
        {
            "template": bucket["template"],
            "count": len(bucket["lines"]),
            "examples": bucket["lines"][:2],
        }
        for bucket in buckets.values()
    ]
    clusters.sort(key=lambda c: c["count"], reverse=True)

    return {"clusters": clusters}