# svc-ingestion/Dockerfile
# Build context MUST be the repo root (set in docker-compose.yml via context: .)
# Three-layer install ensures correct dependency order:
#   1. cafm-connector-service  (ORM models — authoritative source)
#   2. shared-lib              (telemetry, logging, re-exports cafm-connector models)
#   3. svc-ingestion           (this service)

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Layer 1: cafm-connector-service ─────────────────────────────────────────
COPY cafm-connector-service/pyproject.toml /build/cafm-connector-service/
COPY cafm-connector-service/src /build/cafm-connector-service/src/
RUN pip install --no-cache-dir -e "/build/cafm-connector-service[postgresql,files,vault,azure]"

# ── Layer 2: shared-lib ──────────────────────────────────────────────────────
COPY shared-lib /build/shared-lib/
RUN pip install --no-cache-dir -e "/build/shared-lib"

# ── Layer 3: this service ────────────────────────────────────────────────────
COPY svc-ingestion/pyproject.toml ./
COPY svc-ingestion/src ./src/
RUN pip install --no-cache-dir -e "."

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8001

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
