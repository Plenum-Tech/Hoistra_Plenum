# Unified CAFM Platform Dockerfile
# Runs all 4 FastAPI services behind nginx with path-based routing:
#   /connector     → cafm-connector-service  (internal port 8000)
#   /ingestion     → svc-ingestion           (internal port 8001)
#   /query         → svc-query               (internal port 8002)
#   /schema-mapper → svc-ai-schema-mapper    (internal port 8003)
#
# Build context: cafm-connector-service-final/

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl nginx supervisor \
    tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/log/supervisor

WORKDIR /build

# ── Layer 1: cafm-connector-service (ORM models — authoritative) ─────────────
COPY cafm-connector-service/pyproject.toml cafm-connector-service/
COPY cafm-connector-service/src            cafm-connector-service/src/
RUN pip install --no-cache-dir -e "cafm-connector-service[all]"

# ── Layer 2: shared-lib ───────────────────────────────────────────────────────
COPY shared-lib shared-lib/
RUN pip install --no-cache-dir -e "shared-lib"

# ── Layer 3: svc-ingestion ────────────────────────────────────────────────────
COPY svc-ingestion/pyproject.toml svc-ingestion/
COPY svc-ingestion/src             svc-ingestion/src/
RUN pip install --no-cache-dir -e "svc-ingestion"

# ── Layer 4: svc-query ────────────────────────────────────────────────────────
COPY svc-query/pyproject.toml svc-query/
COPY svc-query/src             svc-query/src/
RUN pip install --no-cache-dir -e "svc-query"

# ── Layer 5: svc-ai-schema-mapper ────────────────────────────────────────────
COPY svc-ai-schema-mapper/pyproject.toml svc-ai-schema-mapper/
COPY svc-ai-schema-mapper/src            svc-ai-schema-mapper/src/
COPY svc-ai-schema-mapper/alembic        svc-ai-schema-mapper/alembic/
COPY svc-ai-schema-mapper/alembic.ini    svc-ai-schema-mapper/
RUN pip install --no-cache-dir -e "svc-ai-schema-mapper"

# ── Layer 6: doc-rag-main ─────────────────────────────────────────────────────
COPY doc-rag-main/requirements.txt doc-rag-main/
RUN pip install --no-cache-dir -r "doc-rag-main/requirements.txt"
COPY doc-rag-main doc-rag-main/

# ── Layer 7: svc-work-order-management ───────────────────────────────────────
COPY svc-work-order-management/requirements.txt svc-work-order-management/
RUN pip install --no-cache-dir -r "svc-work-order-management/requirements.txt"
COPY svc-work-order-management/src svc-work-order-management/src/

# ── nginx + supervisord config ────────────────────────────────────────────────
COPY deploy/nginx.conf       /etc/nginx/conf.d/cafm.conf
COPY deploy/supervisord.conf /etc/supervisor/conf.d/cafm.conf

# Remove default nginx site
RUN rm -f /etc/nginx/sites-enabled/default

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 80

CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
