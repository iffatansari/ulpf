import asyncio
import json
from contextlib import suppress
from types import SimpleNamespace

import routes.events as events_mod
from api.live_events import (
    SSE_RESET,
    LiveEventHub,
    decode_normalized_event,
    event_matches_source,
    format_sse_event,
)


def test_live_event_hub_delivers_matching_events_to_subscriber():
    async def scenario():
        hub = LiveEventHub(max_queue_size=4)
        queue = await hub.subscribe("source-1")
        await hub.publish({"event_id": "other", "extensions": {"source_id": "source-2"}})
        await hub.publish({"event_id": "matching", "extensions": {"source_id": "source-1"}})

        assert await asyncio.wait_for(queue.get(), timeout=1) == {
            "event_id": "matching",
            "extensions": {"source_id": "source-1"},
        }

    asyncio.run(scenario())


def test_live_event_hub_supports_unfiltered_subscriber():
    async def scenario():
        hub = LiveEventHub(max_queue_size=4)
        queue = await hub.subscribe()
        await hub.publish({"event_id": "one"})
        await hub.publish({"event_id": "two"})

        assert [await queue.get(), await queue.get()] == [{"event_id": "one"}, {"event_id": "two"}]

    asyncio.run(scenario())


def test_live_event_hub_resets_slow_subscriber():
    async def scenario():
        hub = LiveEventHub(max_queue_size=1)
        queue = await hub.subscribe()
        await hub.publish({"event_id": "old"})
        await hub.publish({"event_id": "new"})

        assert await queue.get() is SSE_RESET

    asyncio.run(scenario())


def test_event_matches_source_accepts_top_level_and_extension_source_ids():
    assert event_matches_source({"source_id": "source-1"}, "source-1")
    assert event_matches_source({"extensions": {"source_id": "source-1"}}, "source-1")
    assert not event_matches_source({"extensions": {"source_id": "source-2"}}, "source-1")


def test_live_event_hub_replays_events_after_last_event_id():
    async def scenario():
        hub = LiveEventHub(max_queue_size=4, replay_size=3)
        await hub.publish({"event_id": "event-1", "extensions": {"source_id": "source-1"}})
        await hub.publish({"event_id": "event-2", "extensions": {"source_id": "source-1"}})
        await hub.publish({"event_id": "event-3", "extensions": {"source_id": "source-2"}})

        assert hub.replay_since("event-1", "source-1") == [
            {"event_id": "event-2", "extensions": {"source_id": "source-1"}}
        ]
        assert hub.replay_since("event-1", "source-2") == [
            {"event_id": "event-3", "extensions": {"source_id": "source-2"}}
        ]
        assert hub.replay_since("unknown") == []

    asyncio.run(scenario())


def test_format_sse_event_contains_id_event_and_json_data():
    event = {"event_id": "event-1", "extensions": {"source_id": "source-1"}}
    frame = format_sse_event(event)

    assert frame.startswith("id: event-1\nevent: normalized\n")
    data_line = next(line for line in frame.splitlines() if line.startswith("data: "))
    assert json.loads(data_line[6:]) == event


def test_event_matches_source_does_not_treat_empty_filter_as_global():
    assert not event_matches_source({"extensions": {"source_id": "source-1"}}, "")


def test_decode_normalized_event_validates_schema_and_size():
    valid_event = {
        "event_id": "event-1",
        "raw_event_id": "raw-1",
        "parser_id": "json-parser-v1",
        "parser_tier": "primary",
        "confidence_score": 0.95,
        "time": "2026-01-01T00:00:00Z",
        "extensions": {"source_id": "source-1"},
    }

    assert decode_normalized_event(json.dumps(valid_event).encode())["event_id"] == "event-1"
    assert decode_normalized_event(json.dumps({"event_id": "bad"}).encode()) is None
    assert decode_normalized_event(b"x" * (1024 * 1024 + 1)) is None


def test_stream_events_cleans_up_when_snapshot_is_cancelled(monkeypatch):
    async def scenario():
        hub = LiveEventHub(max_queue_size=4)
        started = asyncio.Event()

        async def blocked_search(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(events_mod, "run_in_threadpool", blocked_search)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(live_event_hub=hub)))
        task = asyncio.create_task(events_mod.stream_events(request, "source-1"))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert hub._subscribers
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        assert not hub._subscribers

    asyncio.run(scenario())


def test_stream_events_sends_snapshot_then_new_live_events(monkeypatch):
    async def scenario():
        hub = LiveEventHub(max_queue_size=4)

        class FakeES:
            def search(self, index, body):
                assert index == "ulpf-silver"
                assert body["query"]["bool"]["filter"] == [
                    {"term": {"extensions.source_id.keyword": "source-1"}}
                ]
                return {
                    "hits": {
                        "hits": [
                            {"_source": {"event_id": "event-1", "extensions": {"source_id": "source-1"}}},
                            {"_source": {"event_id": "event-0", "extensions": {"source_id": "source-1"}}},
                        ]
                    }
                }

        monkeypatch.setattr(events_mod, "get_opensearch_client", lambda: FakeES())
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(live_event_hub=hub)))
        request.is_disconnected = lambda: asyncio.sleep(0, result=False)

        response = await events_mod.stream_events(request, "source-1")
        await hub.publish({"event_id": "event-1", "extensions": {"source_id": "source-1"}})
        await hub.publish({"event_id": "event-2", "extensions": {"source_id": "source-1"}})
        iterator = response.body_iterator
        assert await anext(iterator) == ": connected\n\n"
        assert "event-0" in await anext(iterator)
        assert "event-1" in await anext(iterator)
        assert "event-2" in await anext(iterator)
        await iterator.aclose()
        assert not hub._subscribers

    asyncio.run(scenario())
