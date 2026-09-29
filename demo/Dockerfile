FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Two requirement files on purpose. The sse collector supplies httpx (the
# producer borrows its format-hint detection), and the orchestrator supplies
# drain3. Installing only the collector's file left this image with no drain3
# at all, so the one generator meant to exercise the fallback tier could not
# have done so however long it ran.
COPY collectors/sse_collector/requirements.txt ./requirements-collectors.txt
COPY orchestrator/requirements.txt ./requirements-orchestrator.txt
RUN pip install --no-cache-dir --no-compile -r requirements-collectors.txt -r requirements-orchestrator.txt \
    && useradd --create-home --uid 10001 simulator

COPY schema ./schema
COPY collectors ./collectors
# The orchestrator is copied so describe() can import the real
# normalize_raw_event and print the verdict each record actually got. Without
# it the ImportError branch silently reduced every line to "hint=syslog",
# which looks like a running pipeline while proving nothing about parsing.
COPY orchestrator ./orchestrator
COPY demo/kafka_live_producer.py ./demo/kafka_live_producer.py

USER simulator

# ENTRYPOINT, not CMD. With a bare CMD any argument after the image name
# REPLACES the command, so `docker run <image> --count 4` tries to exec
# "--count" and dies with a confusing "executable file not found" -- which
# reads like the producer failed for no reason. With ENTRYPOINT the flags
# pass through to the script as intended.
ENTRYPOINT ["python", "demo/kafka_live_producer.py"]
