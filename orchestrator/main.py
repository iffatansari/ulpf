import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from schema.raw_event import RawEventEnvelope
from schema.normalized_event import NormalizedEvent
from schema.dlq_record import DLQRecord

from parsers.syslog_parser import parse_syslog
from parsers.json_parser import parse_json_log
# from parsers.cef_parser import parse_cef  # TODO: implement


REDPANDA_BROKER = os.getenv("REDPANDA_BROKER", "redpanda:29092")
RAW_TOPIC = os.getenv("RAW_TOPIC", "logs.raw")
NORMALIZED_TOPIC = os.getenv("NORMALIZED_TOPIC", "logs.normalized")
DLQ_TOPIC = os.getenv("DLQ_TOPIC", "logs.dlq")

GROUP_ID = "ulpf-orchestrator-group"


def parse_with_chain(raw_event: RawEventEnvelope) -> Optional[NormalizedEvent]:
    """
    Multi-parser chain:
    1) Try format-specific primary parsers based on format_hint.
    2) Try generic JSON parser.
    3) Return None to send to DLQ.
    """
    raw = raw_event.raw_payload
    rid = raw_event.event_id
    source_id = raw_event.source_id

    result: Optional[NormalizedEvent] = None

    # 1) Primary parsers based on hint
    if raw_event.format_hint == "syslog":
        result = parse_syslog(raw, rid, source_id)
    elif raw_event.format_hint == "json":
        result = parse_json_log(raw, rid, source_id)

    if result:
        return result

    # 2) Generic fallback: try JSON anyway
    if raw_event.format_hint != "json":
        result = parse_json_log(raw, rid, source_id)
        if result:
            result.parser_tier = "generic"  # type: ignore
            result.confidence_score = 0.7   # type: ignore
            return result

    # 3) No parser succeeded → DLQ
    return None


def build_dlq_record(raw_event: RawEventEnvelope, parsers_attempted: List[str], classification: str) -> DLQRecord:
    return DLQRecord(
        dlq_id=str(uuid.uuid4()),
        raw_event_id=raw_event.event_id,
        raw_payload=raw_event.raw_payload,
        parsers_attempted=parsers_attempted,
        status="parse_failure",
        classification=classification,
        reprocess_count=0,
        metadata={
            "source_id": raw_event.source_id,
            "format_hint": raw_event.format_hint,
        },
    )


async def main():
    consumer = AIOKafkaConsumer(
        RAW_TOPIC,
        bootstrap_servers=REDPANDA_BROKER,
        group_id=GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda v: v.decode("utf-8"),
    )
    producer = AIOKafkaProducer(bootstrap_servers=REDPANDA_BROKER)

    await consumer.start()
    await producer.start()

    try:
        async for msg in consumer:
            raw_json = json.loads(msg.value)
            raw_event = RawEventEnvelope(**raw_json)

            parsers_attempted = []
            norm: Optional[NormalizedEvent] = None

            # Run parser chain
            if raw_event.format_hint == "syslog":
                parsers_attempted.append("syslog-parser-v1")
                norm = parse_syslog(raw_event.raw_payload, raw_event.event_id, raw_event.source_id)
            elif raw_event.format_hint == "json":
                parsers_attempted.append("json-parser-v1")
                norm = parse_json_log(raw_event.raw_payload, raw_event.event_id, raw_event.source_id)

            if not norm:
                # Try generic JSON as fallback
                parsers_attempted.append("generic-json-parser-v1")
                norm = parse_json_log(raw_event.raw_payload, raw_event.event_id, raw_event.source_id)
                if norm:
                    norm.parser_tier = "generic"  # type: ignore
                    norm.confidence_score = 0.7   # type: ignore

            if norm:
                # Send to normalized topic
                await producer.send_and_wait(
                    NORMALIZED_TOPIC,
                    json.dumps(norm.dict(default=str)).encode("utf-8"),
                )
            else:
                # Send to DLQ
                dlq = build_dlq_record(
                    raw_event,
                    parsers_attempted,
                    classification="no_parser_matched",
                )
                await producer.send_and_wait(
                    DLQ_TOPIC,
                    json.dumps(dlq.dict(default=str)).encode("utf-8"),
                )

    finally:
        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())