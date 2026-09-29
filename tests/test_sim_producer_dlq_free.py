"""
The simulated live stream must not feed the DLQ.

`kafka_live_producer.py` is the only source that runs unattended, so a
generator whose own parser rejects it turns the DLQ page into a permanent
red herring. Every generator in DEFAULT_FORMATS has to normalize to Silver;
the deliberate-failure generator stays opt-in.

Drives the real generators through the real orchestrator parser selection,
one full rotation per seed so the randomized fields are all exercised.
"""

import random

import pytest
from orchestrator.main import normalize_raw_event

from demo import kafka_live_producer as sim

ROUNDS = 8


def build(name: str, rng: random.Random, source_id: str = "sim-test-1"):
    if name == "json":
        payload = sim.gen_json(rng, string_levels=False)
    else:
        payload = sim.GENERATORS[name](rng)
    return sim.build_envelope(
        payload,
        source_id=source_id,
        source_type="application",
        transport="sse",
        collector_id="sim-test-collector",
        hint=sim.FORCED_HINTS.get(name),
    )


@pytest.mark.parametrize("name", sorted(set(sim.GENERATORS) - set(sim.FORCED_HINTS)))
def test_every_non_forced_generator_lands_in_silver(name):
    rng = random.Random(11)
    for _ in range(ROUNDS):
        envelope = build(name, rng)
        normalized, attempted = normalize_raw_event(envelope)
        assert normalized is not None, (
            f"{name} went to the DLQ (hint={envelope.format_hint!r}, "
            f"tried {attempted})"
        )
        assert normalized.parser_id


@pytest.mark.parametrize("name", sorted(sim.FORCED_HINTS))
def test_forced_hints_still_reach_the_dlq(name):
    """The opt-in failure generator has to keep failing, or the DLQ is a lie."""
    rng = random.Random(11)
    normalized, attempted = normalize_raw_event(build(name, rng))
    assert normalized is None
    assert attempted


def test_default_format_mix_is_dlq_free():
    formats = [f.strip() for f in sim.DEFAULT_FORMATS.split(",") if f.strip()]
    assert formats, "DEFAULT_FORMATS must not be empty"
    assert not set(formats) & set(sim.FORCED_HINTS), (
        "the default live-stream mix must not include a deliberate-failure "
        f"generator: {sorted(set(formats) & set(sim.FORCED_HINTS))}"
    )


def test_default_mix_normalizes_end_to_end():
    """One full rotation of the shipped mix, as the container runs it."""
    formats = [f.strip() for f in sim.DEFAULT_FORMATS.split(",") if f.strip()]
    rng = random.Random(11)
    for index in range(ROUNDS * len(formats)):
        envelope = build(formats[index % len(formats)], rng)
        normalized, _ = normalize_raw_event(envelope)
        assert normalized is not None
        # Silver indexes `time`; a generator that drops it would be rejected
        # by OpenSearch and land in the DLQ as index_rejected.
        assert normalized.time is not None
        assert normalized.severity
