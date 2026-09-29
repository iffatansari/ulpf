import asyncio
import json
import os
import uuid
from collections import deque
from contextlib import suppress
from typing import Any, Optional

from aiokafka import AIOKafkaConsumer
from pydantic import ValidationError

from schema.normalized_event import NormalizedEvent

REDPANDA_BROKER = os.getenv("REDPANDA_BROKER", "redpanda:29092")
NORMALIZED_TOPIC = os.getenv("NORMALIZED_TOPIC", "logs.normalized")
SSE_HEARTBEAT_SECONDS = max(0.1, float(os.getenv("SSE_HEARTBEAT_SECONDS", "15")))
SSE_QUEUE_SIZE = max(1, int(os.getenv("SSE_QUEUE_SIZE", "256")))
SSE_REPLAY_SIZE = max(0, int(os.getenv("SSE_REPLAY_SIZE", "1000")))
MAX_SSE_SUBSCRIBERS = max(1, int(os.getenv("MAX_SSE_SUBSCRIBERS", "500")))
MAX_NORMALIZED_MESSAGE_BYTES = max(
    1024, int(os.getenv("MAX_NORMALIZED_MESSAGE_BYTES", str(1024 * 1024)))
)
KAFKA_READY_TIMEOUT_SECONDS = max(
    1.0, float(os.getenv("KAFKA_READY_TIMEOUT_SECONDS", "15"))
)
SSE_RESET = object()


class SSECapacityError(RuntimeError):
    pass



def event_source_id(event: dict[str, Any]) -> Optional[str]:
    value = event.get("source_id")
    if value is not None:
        return str(value)
    extensions = event.get("extensions")
    if isinstance(extensions, dict):
        value = extensions.get("source_id")
        if value is not None:
            return str(value)
    return None


def event_matches_source(event: dict[str, Any], source_id: Optional[str]) -> bool:
    if source_id is None:
        return True
    return event_source_id(event) == source_id


def format_sse_event(event: dict[str, Any]) -> str:
    raw_id = event.get("event_id") or uuid.uuid4().hex
    event_id = str(raw_id).replace("\r", "").replace("\n", "")
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: normalized\ndata: {data}\n\n"


class LiveEventHub:
    def __init__(
        self,
        max_queue_size: int = SSE_QUEUE_SIZE,
        replay_size: int = SSE_REPLAY_SIZE,
    ):
        self.max_queue_size = max_queue_size
        self.replay_size = max(0, replay_size)
        self._subscribers: set[tuple[asyncio.Queue, Optional[str]]] = set()
        self._replay: deque[tuple[str, dict[str, Any]]] = deque(
            maxlen=self.replay_size
        )

    async def subscribe(self, source_id: Optional[str] = None) -> asyncio.Queue:
        if len(self._subscribers) >= MAX_SSE_SUBSCRIBERS:
            raise SSECapacityError("maximum SSE subscriber count reached")
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.max_queue_size)
        self._subscribers.add((queue, source_id))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers = {
            subscriber for subscriber in self._subscribers if subscriber[0] is not queue
        }

    async def reset_subscribers(self) -> None:
        for queue, _source_id in tuple(self._subscribers):
            while not queue.empty():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(SSE_RESET)

    def clear_replay(self) -> None:
        """
        Drop the replay buffer.

        The buffer is in-memory history of events the pipeline has already
        published. Wiping the indices to start a run from zero does NOT
        touch it, so a browser that reconnects with `last-event-id` gets
        pre-wipe events replayed at it and the feed climbs back to a
        non-zero count the dashboard then reports as truth. Clearing the
        buffer is what makes "every counter reads zero" actually true.
        """
        self._replay.clear()

    def replay_since(
        self, last_event_id: Optional[str], source_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        if not last_event_id or self.replay_size == 0:
            return []
        entries = list(self._replay)
        entry_ids = [entry_id for entry_id, _event in entries]
        if last_event_id not in entry_ids:
            return []
        start = entry_ids.index(last_event_id) + 1
        return [
            dict(event)
            for _event_id, event in entries[start:]
            if event_matches_source(event, source_id)
        ]

    async def publish(self, event: dict[str, Any]) -> None:
        event_id = event.get("event_id")
        if event_id is not None and self.replay_size > 0:
            self._replay.append((str(event_id), dict(event)))
        for queue, source_id in tuple(self._subscribers):
            if not event_matches_source(event, source_id):
                continue
            if queue.full():
                while not queue.empty():
                    with suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
                queue.put_nowait(SSE_RESET)
            else:
                queue.put_nowait(event)


def decode_normalized_event(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, (bytes, bytearray)) and len(value) > MAX_NORMALIZED_MESSAGE_BYTES:
        return None
    if isinstance(value, str) and len(value.encode("utf-8")) > MAX_NORMALIZED_MESSAGE_BYTES:
        return None
    try:
        event = json.loads(value)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(event, dict):
        return None
    try:
        return NormalizedEvent.model_validate(event).model_dump(mode="json")
    except ValidationError:
        return None


class NormalizedEventService:
    def __init__(self, hub: LiveEventHub, broker: str = REDPANDA_BROKER, topic: str = NORMALIZED_TOPIC):
        self.hub = hub
        self.broker = broker
        self.topic = topic
        self._task: Optional[asyncio.Task[None]] = None
        self._stopping = asyncio.Event()
        self._ready = asyncio.Event()
        group_prefix = os.getenv("SSE_GROUP_ID", "ulpf-api-sse")
        self._group_id = f"{group_prefix}-{uuid.uuid4().hex}"
        self.consumer: Optional[AIOKafkaConsumer] = None

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    async def start(self) -> None:
        self._stopping.clear()
        self._ready.clear()
        self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(
                self._ready.wait(), timeout=KAFKA_READY_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            await self.stop()
            raise RuntimeError("normalized event consumer did not become ready") from exc

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stopping.is_set():
            consumer: Optional[AIOKafkaConsumer] = None
            try:
                consumer = AIOKafkaConsumer(
                    self.topic,
                    bootstrap_servers=self.broker,
                    group_id=self._group_id,
                    auto_offset_reset="latest",
                    enable_auto_commit=True,
                )
                self.consumer = consumer
                await consumer.start()
                self._ready.set()
                async for message in consumer:
                    if self._stopping.is_set():
                        break
                    event = decode_normalized_event(message.value)
                    if event is None:
                        print("Ignoring invalid normalized event", flush=True)
                        continue
                    await self.hub.publish(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"Live event consumer disconnected: {exc}", flush=True)
                await self.hub.reset_subscribers()
                await asyncio.sleep(2)
            finally:
                self._ready.clear()
                self.consumer = None
                if consumer is not None:
                    with suppress(Exception):
                        await consumer.stop()

    async def publish_message(self, message: Any) -> None:
        event = decode_normalized_event(message.value)
        if event is not None:
            await self.hub.publish(event)
