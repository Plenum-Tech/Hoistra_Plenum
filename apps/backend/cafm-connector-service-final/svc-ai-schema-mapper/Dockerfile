# ── hoist-engine: the Go engine a migration's row-heavy steps run on (src/engine/) ─────────
# Its unit tests run here, so a red one fails the image. The tests that need Postgres (the writer,
# the resolvers, the schema reads, EL-4.0) skip here: before an engine change ships, run them
# against the throwaway parity database (engine/scripts/parity_db.sh up; engine/scripts/dev.sh
# test-db; HOIST_PARITY=1 scripts/test_offline.sh tests/engine_parity). Go 1.25: pgx v5.11 needs it.
FROM golang:1.25-bookworm AS engine
WORKDIR /src
COPY svc-ai-schema-mapper/engine/go.mod svc-ai-schema-mapper/engine/go.sum ./
RUN go mod download
COPY svc-ai-schema-mapper/engine/ ./
RUN CGO_ENABLED=0 go vet ./... && CGO_ENABLED=0 go test ./... \
 && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/hoist-engine ./cmd/hoist-engine

FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (includes doc-rag deps: tesseract, poppler, jpeg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    tesseract-ocr \
    poppler-utils \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Layer 1: cafm-connector-service (ORM models — authoritative)
COPY cafm-connector-service/pyproject.toml /build/cafm-connector-service/
COPY cafm-connector-service/src /build/cafm-connector-service/src/
RUN pip install --no-cache-dir -e "/build/cafm-connector-service[postgresql,files,vault,azure]"

# Layer 2: shared-lib
COPY shared-lib /build/shared-lib/
RUN pip install --no-cache-dir -e "/build/shared-lib"

# Layer 3: this service
COPY svc-ai-schema-mapper/pyproject.toml ./
COPY svc-ai-schema-mapper/src ./src/
COPY svc-ai-schema-mapper/alembic ./alembic/
COPY svc-ai-schema-mapper/alembic.ini ./
RUN pip install --no-cache-dir -e "."

# Layer 4: doc-rag dependencies (packages not already in pyproject.toml)
RUN pip install --no-cache-dir \
    pgvector==0.3.5 \
    pypdf==5.0.1 \
    pdfplumber==0.11.4 \
    pdf2image==1.17.0 \
    Pillow==10.4.0 \
    pytesseract==0.3.13 \
    rank-bm25==0.2.2 \
    numpy \
    scikit-learn \
    tiktoken==0.7.0 \
    loguru==0.7.2 \
    python-docx==1.1.2

# Layer 5: doc-rag source (mounted as volume in dev, copied here for prod)
COPY doc-rag-main /app/doc_rag

# hoist-engine and its data sets (the hoist-engine-cache volume in compose, shared by the app
# and the worker so a run's files are where either process looks).
COPY --from=engine /out/hoist-engine /usr/local/bin/hoist-engine
RUN mkdir -p /var/hoist-engine

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8003

# Match deploy/supervisord.conf + deploy/nginx.conf location /schema-mapper/
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8003", "--root-path", "/schema-mapper"]
