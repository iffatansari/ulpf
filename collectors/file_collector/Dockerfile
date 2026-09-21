FROM python:3.11-slim

WORKDIR /app

COPY collectors/file_collector/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY schema ./schema
COPY collectors/file_collector ./collector

ENV PYTHONPATH=/app

WORKDIR /app/collector

CMD ["python", "main.py"]