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

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8003

# Match deploy/supervisord.conf + deploy/nginx.conf location /schema-mapper/
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8003", "--root-path", "/schema-mapper"]
