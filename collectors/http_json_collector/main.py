import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, Request

from schema.raw_event import RawEventEnvelope


REDPANDA_BROKER = os.getenv("REDPANDA_BROKER", "redpanda:29092")
RAW_TOPIC = os.getenv("RAW_TOPIC", "logs.raw")
COLLECTOR_ID = os.getenv("COLLECTOR_ID", "http-json-collector-1")
HTTP_PORT = int(os.getenv("HTTP_PORT", "8081"))


async def send_raw_event(
    producer: AIOKafkaProducer,
    raw_json: dict,
    source_id: str,
    source_type: str,
    transport: str,
    format_hint: Optional[str],
):
    event = RawEventEnvelope(
        event_id=str(uuid.uuid4()),
        source_id=source_id,
        source_type=source_type,
        transport=transport,
        format_hint=format_hint,
        raw_payload=json.dumps(raw_json),
        collector_id=COLLECTOR_ID,
    )

    await producer.send_and_wait(
        RAW_TOPIC,
        json.dumps(event.model_dump(mode="json")).encode("utf-8"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    producer = AIOKafkaProducer(
        bootstrap_servers=REDPANDA_BROKER
    )

    await producer.start()
    app.state.producer = producer

    try:
        yield
    finally:
        await producer.stop()


app = FastAPI(lifespan=lifespan)


@app.post("/logs")
async def ingest_logs(request: Request):
    producer: AIOKafkaProducer = request.app.state.producer

    data = await request.json()

    source_id = data.get(
        "source_id",
        "http-json-source-1"
    )

    source_type = data.get(
        "source_type",
        "application"
    )

    events = data.get("events", [])

    if isinstance(events, dict):
        events = [events]

    for ev in events:
        await send_raw_event(
            producer=producer,
            raw_json=ev,
            source_id=source_id,
            source_type=source_type,
            transport="http_json",
            format_hint="json",
        )

    return {
        "status": "ok",
        "ingested": len(events)
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=HTTP_PORT
    )
