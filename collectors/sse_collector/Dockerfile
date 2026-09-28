FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY collectors/sse_collector/requirements.txt ./
RUN pip install --no-cache-dir --no-compile -r requirements.txt \
    && useradd --create-home --uid 10001 collector

COPY schema ./schema
COPY collectors/sse_collector ./collector

USER collector

WORKDIR /app/collector

CMD ["python", "main.py"]
