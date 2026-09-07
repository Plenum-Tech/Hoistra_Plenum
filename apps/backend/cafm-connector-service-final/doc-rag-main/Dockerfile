# ---------- Base image ----------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps for pdfplumber, psycopg, and image/OCR pipeline
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libjpeg-dev \
        zlib1g-dev \
        curl \
        tesseract-ocr \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cache layer)
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy the app
COPY . .

# Vendor Swagger UI + ReDoc assets so /docs and /redoc render even on
# offline machines, behind corporate proxies, or with ad blockers that
# strip cdn.jsdelivr.net. If the build is offline and the download
# fails, the runtime falls back to the CDN automatically — so we make
# this step non-fatal.
RUN chmod +x scripts/fetch_docs_assets.sh && \
    (bash scripts/fetch_docs_assets.sh || \
     echo "WARN: docs asset fetch failed; /docs will use CDN fallback at runtime")

# Make sure runtime dirs exist
RUN mkdir -p /app/data/uploads /app/logs

EXPOSE 8000 8501

# Default command runs the API. docker-compose overrides this for the UI.
# Match deploy/supervisord.conf + deploy/nginx.conf location /doc-rag/ (internal port 8004 there)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--root-path", "/doc-rag"]
