import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from schema.raw_event import RawEventEnvelope


REDPANDA_BROKER = os.getenv(
    "REDPANDA_BROKER",
    "redpanda:29092",
)

RAW_TOPIC = os.getenv(
    "RAW_TOPIC",
    "logs.raw",
)

COLLECTOR_ID = os.getenv(
    "COLLECTOR_ID",
    "http-json-collector-1",
)

HTTP_PORT = int(
    os.getenv(
        "HTTP_PORT",
        "8081",
    )
)


async def send_raw_payload(
    producer: AIOKafkaProducer,
    raw_payload: str,
    source_id: str,
    source_type: str,
    format_hint: Optional[str],
):
    """
    Put the original HTTP payload into the raw pipeline.

    The payload is kept as a string so even malformed JSON
    can be preserved and handled later by the orchestrator.
    """

    event = RawEventEnvelope(
        event_id=str(uuid.uuid4()),
        source_id=source_id,
        source_type=source_type,
        transport="http",
        format_hint=format_hint,
        raw_payload=raw_payload,
        collector_id=COLLECTOR_ID,
    )

    await producer.send_and_wait(
        RAW_TOPIC,
        json.dumps(
            event.model_dump(mode="json")
        ).encode("utf-8"),
    )

    return event


async def send_raw_event(
    producer: AIOKafkaProducer,
    raw_json: dict,
    source_id: str,
    source_type: str,
):
    """
    Send one valid JSON event to the raw pipeline.
    """

    raw_payload = json.dumps(
        raw_json,
        separators=(",", ":"),
    )

    return await send_raw_payload(
        producer=producer,
        raw_payload=raw_payload,
        source_id=source_id,
        source_type=source_type,
        format_hint="json",
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


app = FastAPI(
    lifespan=lifespan
)


@app.post("/logs")
async def ingest_logs(request: Request):
    producer: AIOKafkaProducer = (
        request.app.state.producer
    )

    # Read the original request body first.
    # This allows us to preserve malformed JSON.
    body = await request.body()

    raw_body = body.decode(
        "utf-8",
        errors="replace",
    )

    try:
        data = json.loads(raw_body)

    except json.JSONDecodeError as exc:
        # The HTTP payload is malformed JSON.
        # Preserve it in logs.raw so the orchestrator can
        # classify it and send it to DLQ.

        event = await send_raw_payload(
            producer=producer,
            raw_payload=raw_body,
            source_id="http-json-source-1",
            source_type="application",
            format_hint="json",
        )

        print(
            "Malformed JSON preserved in raw pipeline: "
            f"{event.event_id}",
            flush=True,
        )

        return JSONResponse(
            status_code=400,
            content={
                "status": "rejected",
                "reason": "invalid_json",
                "message": (
                    "Request body was preserved and "
                    "sent to the processing pipeline."
                ),
                "raw_event_id": event.event_id,
            },
        )

    source_id = data.get(
        "source_id",
        "http-json-source-1",
    )

    source_type = data.get(
        "source_type",
        "application",
    )

    events = data.get(
        "events",
        [],
    )

    if isinstance(events, dict):
        events = [events]

    if not isinstance(events, list):
        return JSONResponse(
            status_code=400,
            content={
                "status": "rejected",
                "reason": "invalid_events_field",
                "message": (
                    "'events' must be a JSON object "
                    "or an array of objects."
                ),
            },
        )

    for ev in events:

        if not isinstance(ev, dict):
            return JSONResponse(
                status_code=400,
                content={
                    "status": "rejected",
                    "reason": "invalid_event",
                    "message": (
                        "Each event must be a JSON object."
                    ),
                },
            )

        await send_raw_event(
            producer=producer,
            raw_json=ev,
            source_id=source_id,
            source_type=source_type,
        )

    return {
        "status": "ok",
        "ingested": len(events),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=HTTP_PORT,
    )