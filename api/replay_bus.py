"""
Kafka publisher used by the Module 3 replay endpoints.

Kept separate from `live_events.py` because it has a different job: that hub
consumes Silver for the UI's live feed, while this one publishes Bronze
events back onto `logs.raw` so the orchestrator can reprocess them.

There is deliberately no Kafka consumer here. The API must not observe or
judge a replay result -- that is the orchestrator's job, and the UI reads the
outcome from OpenSearch. Letting the API also watch the topic would create a
second place that believes it knows whether a replay succeeded.
"""

import os

from aiokafka import AIOKafkaProducer


class ReplayPublisher:
    """
    Thin lifecycle wrapper around a single AIOKafkaProducer.

    One long-lived producer is shared by all requests: creating a producer per
    request would pay a full metadata fetch and connection setup on every
    replay, and Kafka producers are designed to be reused.
    """

    def __init__(self, broker: str | None = None):
        self._broker = broker or os.getenv("REDPANDA_BROKER", "redpanda:29092")
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        if self._producer is not None:
            return
        producer = AIOKafkaProducer(bootstrap_servers=self._broker)
        await producer.start()
        self._producer = producer

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    @property
    def producer(self) -> AIOKafkaProducer:
        if self._producer is None:
            raise RuntimeError("ReplayPublisher used before start()")
        return self._producer
