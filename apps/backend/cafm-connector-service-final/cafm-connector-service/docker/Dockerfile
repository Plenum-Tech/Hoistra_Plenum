FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Copy project files — build context must be cafm-connector-service/ (not docker/)
COPY pyproject.toml .
COPY src/ ./src/

# Install Python deps (all extras including azure-storage-blob)
RUN pip install --no-cache-dir -e ".[all]"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Default: run API server
# For the worker container, override with:
# arq cafm_connector.jobs.worker.WorkerSettings
CMD ["uvicorn", "cafm_connector.api.app:app", "--host", "0.0.0.0", "--port", "8000"]