FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY collectors/sse_collector/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --no-compile -r requirements.txt opensearch-py==3.2.0 \
    && useradd --create-home --uid 10001 simulator

COPY schema ./schema
COPY collectors ./collectors
COPY demo/kafka_live_producer.py ./demo/kafka_live_producer.py

USER simulator

CMD ["python", "demo/kafka_live_producer.py"]
