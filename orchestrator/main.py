import asyncio
import json
import os
from typing import Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from opensearchpy import OpenSearch
from schema.raw_event import RawEventEnvelope
from schema.normalized_event import NormalizedEvent

from parsers.json_parser import parse_json_log
from parsers.syslog_parser import parse_syslog


REDPANDA_BROKER = os.getenv("REDPANDA_BROKER", "redpanda:29092")
RAW_TOPIC = os.getenv("RAW_TOPIC", "logs.raw")
NORMALIZED_TOPIC = os.getenv("NORMALIZED_TOPIC", "logs.normalized")
OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://opensearch:9200")
SILVER_INDEX = os.getenv("SILVER_INDEX", "ulpf-silver")

GROUP_ID = "ulpf-orchestrator-group"


def normalize_raw_event(raw_event: RawEventEnvelope) -> Optional[NormalizedEvent]:
    if raw_event.transport == "syslog" or raw_event.format_hint == "syslog":
        parser = parse_syslog
    elif raw_event.transport == "http_json" or raw_event.format_hint == "json":
        parser = parse_json_log
    else:
        return None

    parsed = parser(
        raw_event.raw_payload,
        raw_event.event_id,
        raw_event.source_id,
    )
    if parsed is None:
        return None

    return NormalizedEvent.model_validate(parsed.model_dump())


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
    opensearch = OpenSearch(hosts=[OPENSEARCH_URL], timeout=10)

    await consumer.start()
    await producer.start()

    try:
        async for msg in consumer:
            raw_json = json.loads(msg.value)
            raw_event = RawEventEnvelope(**raw_json)
            normalized = normalize_raw_event(raw_event)
            if normalized is None:
                if raw_event.transport == "syslog" or raw_event.format_hint == "syslog":
                    parser_name = "syslog"
                elif raw_event.transport == "http_json" or raw_event.format_hint == "json":
                    parser_name = "json"
                else:
                    parser_name = "none"
                print(
                    f"{parser_name} parser did not match event {raw_event.event_id} "
                    f"(transport={raw_event.transport}, format_hint={raw_event.format_hint})",
                    flush=True,
                )
                continue

            normalized_document = normalized.model_dump(mode="json")

            await producer.send_and_wait(
                NORMALIZED_TOPIC,
                json.dumps(normalized_document).encode("utf-8"),
            )

            opensearch.index(
                index=SILVER_INDEX,
                id=normalized.event_id,
                body=normalized_document,
            )

    finally:
        await consumer.stop()
        await producer.stop()


if __name__ == "__main__":
    asyncio.run(main())