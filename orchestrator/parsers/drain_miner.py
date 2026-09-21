"""
orchestrator/parsers/drain_fallback.py — Phase 2.3, ML fallback tier.

Only reached when the primary AND secondary parsers both fail to
match a line. Mines/matches a template live from the stream and
extracts the variable values in template order.

IMPORTANT, verified by testing (not assumed): Drain3's default
similarity threshold (0.4) will happily merge lines that differ in a
semantically important word — e.g. "...accepted..." and "...denied..."
collapsed into ONE template with the accept/deny status turned into a
wildcard, on just 4 sample lines. For security logs, that's exactly
the kind of word you don't want silently generalized away. Raising
drain_sim_th to ~0.6 keeps such lines in separate templates instead.
Tune this against your real demo log samples, not blindly — 0.6 is a
tested starting point, not guaranteed correct for your data.
"""

from dataclasses import dataclass, field

from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig


@dataclass
class DrainResult:
    matched: bool
    template: str | None = None
    cluster_id: int | None = None
    variables: list = field(default_factory=list)  # ordered list of extracted string values


def _build_miner() -> TemplateMiner:
    config = TemplateMinerConfig()
    config.drain_sim_th = 0.6  # see module docstring — tested, not default
    config.drain_depth = 4
    config.masking_instructions = [
        MaskingInstruction(r'(?<=src=)(?:\d{1,3}\.){3}\d{1,3}', 'IP'),
        MaskingInstruction(r'(?<=dst=)(?:\d{1,3}\.){3}\d{1,3}', 'IP'),
        MaskingInstruction(r'(?<=user=)[^ ]+', 'USER'),
        MaskingInstruction(r'(?<=action=)[^ ]+', 'ACTION'),
        MaskingInstruction(r'(?<=port=)\d{1,5}', 'PORT'),
    ]
    # Persistence intentionally left as in-memory default for the MVP:
    # templates reset on restart. Fine for a demo; swap in
    # drain3.file_persistence.FilePersistence before anything longer-lived.
    return TemplateMiner(config=config)


# One shared miner instance for the process — it needs to accumulate
# templates across every line it sees, not per-call.
_miner = _build_miner()


def parse(raw_payload: str) -> DrainResult:
    """
    Feeds the line into the live miner (so it keeps learning) and
    returns the matched/mined template plus extracted variable values
    in left-to-right order matching the <*> positions in the template.
    """
    result = _miner.add_log_message(raw_payload)
    template = result["template_mined"]
    cluster_id = result["cluster_id"]

    params = _miner.extract_parameters(template, raw_payload, exact_matching=True)
    variables = [p.value for p in params] if params else []

    return DrainResult(matched=True, template=template, cluster_id=cluster_id, variables=variables)