# ULPF
### Universal Log Pre-Processing Framework

A containerized framework for collecting logs from different sources, processing them through a common pipeline, normalizing events, and preparing them for further analysis.

**Pipeline:**  
`Log Source → Collector → Redpanda → Normalizer → OpenSearch`

---

## Quick Start

### Prerequisites
- Git
- Docker Desktop

### 1. Clone

```bash
git clone https://github.com/iffatansari/ulpf.git
cd ulpf
```

### 2. Start ULPF - Run

```bash
docker compose up --build -d
```

### 3. Check the services

```bash
docker compose ps
```
All services should be Up. Redpanda and OpenSearch should show Healthy.

### 4. Open the applications

- UI: http://localhost:3000
- API: http://localhost:8000
- HTTP Log Collector: http://localhost:8081

**ULPF is ready to use.**


This is the version I'd actually put on GitHub. It looks clean without turning the README into a 20-page documentation file.