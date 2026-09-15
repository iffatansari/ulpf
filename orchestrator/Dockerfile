FROM python:3.11-slim

WORKDIR /app

COPY orchestrator/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY schema ./schema
COPY orchestrator ./orchestrator

ENV PYTHONPATH=/app

WORKDIR /app/orchestrator

CMD ["python", "main.py"]
