# Docker Setup & Deployment Guide

## Quick Start (Single Service)

### 1. Build Docker Image
```bash
cd cafm-connector-service-final
docker build -f svc-ai-schema-mapper/Dockerfile -t plenum/svc-ai-schema-mapper:latest .
```

### 2. Run Container
```bash
docker run -d \
  --name svc-ai-schema-mapper \
  -p 8003:8003 \
  -e ANTHROPIC_API_KEY="your-anthropic-key" \
  -e OPENAI_API_KEY="your-openai-key" \
  -e LANGSMITH_API_KEY="your-langsmith-key" \
  -e DB_URL="postgresql+asyncpg://user:password@postgres:5432/cafm" \
  -e REDIS_URL="redis://redis:6379/0" \
  plenum/svc-ai-schema-mapper:latest
```

### 3. Verify Health
```bash
curl http://localhost:8003/health
# Expected response: {"status":"ok"}
```

---

## Full Stack (Docker Compose)

### 1. Create `.env` File
```bash
cd cafm-connector-service-final
cat > .env << 'EOF'
# Infrastructure
DB_URL=postgresql+asyncpg://cafm:cafm@postgres:5432/cafm_connectors
REDIS_URL=redis://redis:6379/0

# API Keys (add your actual keys)
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
LANGSMITH_API_KEY=ls-...

# Azure (optional)
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;...
AZURE_BLOB_CONTAINER_NAME=plenum-agentic-ai-attachments

# Environment
ENVIRONMENT=development
DEBUG=false
EOF
```

### 2. Start All Services
```bash
docker compose up -d
```

This starts:
- **postgres** (5432) — Database
- **redis** (6379) — Session cache & ARQ jobs
- **tempo** (3200, 4317) — Trace collection
- **prometheus** (9090) — Metrics
- **grafana** (3000) — Dashboards (admin/cafm-dev)
- **svc-ai-schema-mapper** (8003) — ✨ Your service

### 3. Wait for Health Checks
```bash
# Monitor startup
docker compose logs -f svc-ai-schema-mapper

# Expected output:
# svc-ai-schema-mapper  | INFO:     Uvicorn running on http://0.0.0.0:8003
# svc-ai-schema-mapper  | INFO:     Application startup complete
```

### 4. Test the Service
```bash
# Health check
curl http://localhost:8003/health

# Metrics
curl http://localhost:8003/metrics

# Start a migration (example)
curl -X POST http://localhost:8003/api/migration/start \
  -H "Content-Type: application/json" \
  -d '{
    "source_blob_url": "https://example.blob.core.windows.net/uploads/assets.csv",
    "source_system": "Maximo",
    "customer_id": "customer-123"
  }'
```

### 5. View Logs
```bash
# All services
docker compose logs -f

# Single service
docker compose logs -f svc-ai-schema-mapper

# With timestamps
docker compose logs -f --timestamps svc-ai-schema-mapper
```

### 6. Stop All Services
```bash
docker compose down

# With volume cleanup (removes data)
docker compose down -v
```

---

## Dockerfile Fixes Required

### Issue 1: Module Path in CMD
**Current (line 31):**
```dockerfile
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8003", "--reload"]
```

**Should be:**
```dockerfile
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8003"]
```

### Issue 2: Python 3.12 Requirement
Dockerfile requires Python 3.12, but your environment has 3.11. Options:

**Option A:** Use Python 3.11 image
```dockerfile
FROM python:3.11-slim
```

**Option B:** Update pyproject.toml
```toml
requires-python = ">=3.11"
```

### Updated Dockerfile
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Layer 1: cafm-connector-service (ORM models)
COPY cafm-connector-service/pyproject.toml /build/cafm-connector-service/
COPY cafm-connector-service/src /build/cafm-connector-service/src/
RUN pip install --no-cache-dir -e "/build/cafm-connector-service" 2>&1 | grep -v "already satisfied" || true

# Layer 2: shared-lib
COPY shared-lib /build/shared-lib/
RUN pip install --no-cache-dir -e "/build/shared-lib" 2>&1 | grep -v "already satisfied" || true

# Layer 3: svc-ai-schema-mapper
COPY svc-ai-schema-mapper/pyproject.toml ./
COPY svc-ai-schema-mapper/src ./src/
COPY svc-ai-schema-mapper/alembic ./alembic/
COPY svc-ai-schema-mapper/alembic.ini ./
RUN pip install --no-cache-dir -e "." 2>&1 | grep -v "already satisfied" || true

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8003

# Fixed CMD with correct module path
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8003"]
```

---

## Docker Compose Override (Optional)

Create `docker-compose.override.yml` for local development:

```yaml
version: '3.8'

services:
  svc-ai-schema-mapper:
    # Mount source code for live reload during development
    volumes:
      - ./svc-ai-schema-mapper/src:/app/src
      - ./shared-lib:/app/shared-lib
    # Use reload mode for development
    command: uvicorn src.app:app --host 0.0.0.0 --port 8003 --reload
    # Add debugging port
    ports:
      - "8003:8003"
      - "5678:5678"  # debugpy for remote debugging
```

Run with:
```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d svc-ai-schema-mapper
```

---

## Common Docker Commands

### Build
```bash
# Build single service
docker build -f svc-ai-schema-mapper/Dockerfile -t svc-ai-schema-mapper:latest .

# Build with compose
docker compose build svc-ai-schema-mapper

# Build from scratch (no cache)
docker compose build --no-cache svc-ai-schema-mapper
```

### Run
```bash
# Start service
docker compose up -d svc-ai-schema-mapper

# Start with logs
docker compose up svc-ai-schema-mapper

# Restart service
docker compose restart svc-ai-schema-mapper
```

### Inspect
```bash
# List running containers
docker compose ps

# View service logs
docker compose logs svc-ai-schema-mapper

# Follow logs in real-time
docker compose logs -f svc-ai-schema-mapper

# Logs from specific time
docker compose logs --since 5m svc-ai-schema-mapper

# Shell into container
docker compose exec svc-ai-schema-mapper bash

# View environment variables
docker compose exec svc-ai-schema-mapper env | grep -E "API_KEY|DB_URL"
```

### Debug
```bash
# Check service status
docker compose ps svc-ai-schema-mapper

# View container details
docker inspect svc-ai-schema-mapper

# Check network connectivity
docker compose exec svc-ai-schema-mapper curl http://postgres:5432/

# Test database connection
docker compose exec svc-ai-schema-mapper psql postgresql://cafm:cafm@postgres:5432/cafm_connectors -c "SELECT 1"

# Test Redis connection
docker compose exec svc-ai-schema-mapper redis-cli -h redis ping
```

### Clean Up
```bash
# Stop all services
docker compose down

# Remove all volumes (WARNING: deletes data)
docker compose down -v

# Remove unused images
docker image prune

# Remove all unused resources
docker system prune -a
```

---

## Environment Variables

### Required
```bash
ANTHROPIC_API_KEY          # Claude API key for embeddings & analysis
OPENAI_API_KEY             # OpenAI API key for embeddings
LANGSMITH_API_KEY          # LangSmith key for trace observability
```

### Database & Cache
```bash
DB_URL=postgresql+asyncpg://cafm:cafm@postgres:5432/cafm_connectors
REDIS_URL=redis://redis:6379/0
```

### Observability
```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
OTEL_SERVICE_NAME=cafm-schema-mapper-service
LANGSMITH_TRACING=true
```

### Optional
```bash
AZURE_STORAGE_CONNECTION_STRING=...
AZURE_BLOB_CONTAINER_NAME=plenum-agentic-ai-attachments
ENVIRONMENT=development
DEBUG=false
```

---

## Port Mapping

| Service | Port | Purpose |
|---------|------|---------|
| svc-ai-schema-mapper | 8003 | REST API + WebSocket |
| postgres | 5432 | Database |
| redis | 6379 | Cache & jobs |
| tempo | 3200, 4317 | Trace collection |
| prometheus | 9090 | Metrics |
| grafana | 3000 | Dashboards |

---

## Running Tests in Docker

### Run Tests Inside Container
```bash
docker compose exec svc-ai-schema-mapper bash
cd /app
pytest tests/ -v
```

### Run Tests with Coverage
```bash
docker compose exec svc-ai-schema-mapper \
  pytest tests/ --cov=src --cov-report=html
```

### Copy Results to Host
```bash
docker compose cp svc-ai-schema-mapper:/app/htmlcov ./test-coverage
```

---

## Database Migrations (Alembic)

### Run Migrations Automatically
```bash
# On startup (update Dockerfile CMD):
CMD sh -c "alembic upgrade head && \
           uvicorn src.app:app --host 0.0.0.0 --port 8003"
```

### Run Migrations Manually
```bash
docker compose exec svc-ai-schema-mapper alembic upgrade head
docker compose exec svc-ai-schema-mapper alembic history
```

### Create New Migration
```bash
docker compose exec svc-ai-schema-mapper \
  alembic revision --autogenerate -m "Add new column"
```

---

## Troubleshooting

### Port Already in Use
```bash
# Find process using port 8003
lsof -i :8003

# Kill process
kill -9 <PID>

# Or use different port in override
ports:
  - "8004:8003"
```

### Container Won't Start
```bash
# Check logs
docker compose logs svc-ai-schema-mapper

# Check image was built
docker image ls | grep svc-ai-schema-mapper

# Rebuild image
docker compose build --no-cache svc-ai-schema-mapper
```

### Database Connection Failed
```bash
# Verify postgres is healthy
docker compose ps postgres

# Check connection string
docker compose exec svc-ai-schema-mapper echo $DB_URL

# Test manually
docker compose exec postgres psql postgresql://cafm:cafm@postgres:5432/cafm_connectors -c "SELECT 1"
```

### Missing Environment Variables
```bash
# Check what's set in container
docker compose exec svc-ai-schema-mapper env | sort

# Add to .env file
echo "ANTHROPIC_API_KEY=your-key" >> .env

# Restart service
docker compose up -d svc-ai-schema-mapper
```

---

## Performance Optimization

### Production Dockerfile
```dockerfile
# Use multi-stage build
FROM python:3.11-slim AS builder

WORKDIR /build
COPY svc-ai-schema-mapper/pyproject.toml .
RUN pip install --user --no-cache-dir -e .

# Production stage
FROM python:3.11-slim

WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY svc-ai-schema-mapper/src ./src

ENV PATH=/root/.local/bin:$PATH
EXPOSE 8003

CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "4"]
```

### Production Compose
```yaml
svc-ai-schema-mapper:
  image: plenum/svc-ai-schema-mapper:latest
  restart: always
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8003/health"]
    interval: 10s
    timeout: 5s
    retries: 3
    start_period: 30s
  deploy:
    resources:
      limits:
        cpus: '1.0'
        memory: 1G
      reservations:
        cpus: '0.5'
        memory: 512M
```

---

## Next Steps

1. **Apply Dockerfile fix** (update Python version and CMD)
2. **Build image:** `docker compose build svc-ai-schema-mapper`
3. **Start services:** `docker compose up -d`
4. **Test endpoints:** `curl http://localhost:8003/health`
5. **Run tests:** `docker compose exec svc-ai-schema-mapper pytest tests/`
6. **View traces:** Open http://localhost:3000 (Grafana)

---

**Last Updated:** 2026-04-02  
**Status:** Ready for deployment
