import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, HTTPException, Request
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

DEFAULT_SOURCE_ID = os.getenv(
    "DEFAULT_SOURCE_ID",
    "http-json-source-1",
)

DEFAULT_SOURCE_TYPE = os.getenv(
    "DEFAULT_SOURCE_TYPE",
    "application",
)

VALID_SOURCE_TYPES = {
    "network_device",
    "server",
    "application",
    "database",
    "cloud",
    "iot",
    "custom",
}

HTTP_PORT = int(
    os.getenv(
        "HTTP_PORT",
        "8081",
    )
)
MAX_HTTP_BODY_BYTES = max(
    1024,
    int(os.getenv("MAX_HTTP_BODY_BYTES", str(10 * 1024 * 1024))),
)
MAX_EVENTS_PER_REQUEST = max(
    1,
    int(os.getenv("MAX_EVENTS_PER_REQUEST", "1000")),
)


async def read_limited_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_HTTP_BODY_BYTES:
                raise HTTPException(status_code=413, detail="request body is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid content-length") from exc

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_HTTP_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body is too large")
        chunks.append(chunk)
    return b"".join(chunks)


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
    body = await read_limited_body(request)

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
            source_id=DEFAULT_SOURCE_ID,
            source_type=DEFAULT_SOURCE_TYPE,
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

    if not isinstance(data, dict):
        return JSONResponse(
            status_code=400,
            content={
                "status": "rejected",
                "reason": "invalid_payload",
                "message": "Request body must be a JSON object.",
            },
        )

    source_id = data.get(
        "source_id",
        DEFAULT_SOURCE_ID,
    )

    source_type = data.get(
        "source_type",
        DEFAULT_SOURCE_TYPE,
    )

    if isinstance(source_id, str):
        source_id = source_id.strip()
    if isinstance(source_type, str):
        source_type = source_type.strip()

    if not isinstance(source_id, str) or not source_id or len(source_id) > 200:
        return JSONResponse(
            status_code=400,
            content={
                "status": "rejected",
                "reason": "invalid_source_id",
                "message": "source_id must be a non-empty string up to 200 characters.",
            },
        )

    if not isinstance(source_type, str) or source_type not in VALID_SOURCE_TYPES:
        return JSONResponse(
            status_code=400,
            content={
                "status": "rejected",
                "reason": "invalid_source_type",
                "message": "source_type is not supported.",
            },
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

    if len(events) > MAX_EVENTS_PER_REQUEST:
        return JSONResponse(
            status_code=413,
            content={
                "status": "rejected",
                "reason": "too_many_events",
                "message": f"events must contain at most {MAX_EVENTS_PER_REQUEST} items.",
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