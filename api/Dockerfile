FROM python:3.11-slim

WORKDIR /app

COPY api/requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY orchestrator ./orchestrator
COPY schema ./schema

ENV PYTHONPATH=/app

WORKDIR /app/api

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]