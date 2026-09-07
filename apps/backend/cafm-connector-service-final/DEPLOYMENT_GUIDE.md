# Complete Deployment Guide — svc-ai-schema-mapper

**Status:** ✅ Ready for Deployment  
**Date:** 2026-04-02

---

## TL;DR — Get Started in 2 Minutes

```bash
cd cafm-connector-service-final

# Create and configure .env
cp .env.example .env
nano .env  # Add your API keys

# Run everything
./docker-build.sh

# Verify
curl http://localhost:8003/health
```

---

## What's Ready

### ✅ Service (svc-ai-schema-mapper)
- **8 REST API endpoints** for migration lifecycle
- **1 WebSocket endpoint** for real-time status
- **9-node LangGraph pipeline** with checkpoint persistence
- **3 HITL gates** for human approval (GATE 1, 2, 3)
- **10 evaluation layers** (EL-M.1 through M.9, EL-3.0)
- **LangSmith observability** with trace URLs
- **PostgreSQL persistence** with Alembic migrations

### ✅ Infrastructure
- **PostgreSQL 16** database with async driver
- **Redis** for session cache and ARQ job queue
- **Tempo** for distributed trace collection
- **Prometheus** for metrics
- **Grafana** for dashboards

### ✅ Testing
- **61 integration tests** (API, WebSocket, E2E)
- **28 E2E pipeline tests** covering all 9 nodes
- **Sample CSV data** (60 rows, 12 columns)
- **Test fixtures** for in-memory SQLite

### ✅ Documentation
- `DOCKER_SETUP.md` — Full Docker reference
- `DOCKER_QUICKREF.md` — Quick command cheatsheet
- `PHASE_8_TESTS.md` — Test suite documentation
- `PROGRESS_REPORT.md` — Architecture & design details

---

## Step 1: Prerequisites

### Required
- **Docker Desktop** (Windows/Mac) or **Docker Engine** (Linux)
- **Docker Compose** (v2+)
- **4GB RAM** minimum
- **API Keys:**
  - Anthropic API key
  - OpenAI API key
  - LangSmith API key

### Optional
- **PostgreSQL client** (psql) for DB inspection
- **Redis CLI** for cache inspection

---

## Step 2: Configuration

### Create `.env` File

```bash
cd cafm-connector-service-final
cat > .env << 'EOF'
# Infrastructure
DB_URL=postgresql+asyncpg://cafm:cafm@postgres:5432/cafm_connectors
REDIS_URL=redis://redis:6379/0

# API Keys (REQUIRED)
ANTHROPIC_API_KEY=sk-ant-...       # Get from https://console.anthropic.com
OPENAI_API_KEY=sk-...              # Get from https://platform.openai.com
LANGSMITH_API_KEY=ls-...           # Get from https://smith.langchain.com

# Azure Storage (optional)
AZURE_STORAGE_CONNECTION_STRING=
AZURE_BLOB_CONTAINER_NAME=plenum-agentic-ai-attachments

# Environment
ENVIRONMENT=development
DEBUG=false
EOF
```

### Verify Configuration
```bash
# Check .env file exists
test -f .env && echo "✓ .env found" || echo "✗ Missing .env"

# Verify required keys are set
grep "^ANTHROPIC_API_KEY" .env && echo "✓ ANTHROPIC_API_KEY set"
grep "^OPENAI_API_KEY" .env && echo "✓ OPENAI_API_KEY set"
grep "^LANGSMITH_API_KEY" .env && echo "✓ LANGSMITH_API_KEY set"
```

---

## Step 3: Build & Deploy

### Automated (Recommended)
```bash
# One command does everything
./docker-build.sh

# It will:
# 1. Check Docker is installed
# 2. Verify .env file
# 3. Build Docker image
# 4. Start all services
# 5. Wait for health checks
# 6. Test connectivity
# 7. Show summary
```

### Manual Build
```bash
# Build image
docker build \
  -f svc-ai-schema-mapper/Dockerfile \
  -t plenum/svc-ai-schema-mapper:latest \
  .

# Start services
docker compose up -d

# Wait for startup (watch logs)
docker compose logs -f svc-ai-schema-mapper
```

---

## Step 4: Verify Deployment

### Check Services Running
```bash
docker compose ps
# Should show: postgres, redis, tempo, prometheus, grafana, svc-ai-schema-mapper all "running"
```

### Test Health Endpoints
```bash
# Service health
curl http://localhost:8003/health
# Expected: {"status":"ok"}

# Metrics
curl http://localhost:8003/metrics
# Expected: Prometheus-format metrics

# Database
docker compose exec postgres psql -U cafm -d cafm_connectors -c "SELECT 1"
# Expected: (1 row) with value 1

# Redis
docker compose exec redis redis-cli ping
# Expected: PONG
```

### View Real-Time Logs
```bash
# All services
docker compose logs -f

# Single service
docker compose logs -f svc-ai-schema-mapper

# Last 100 lines
docker compose logs --tail 100 svc-ai-schema-mapper
```

---

## Step 5: Test the Service

### Start a Migration Job
```bash
# Create migration
curl -X POST http://localhost:8003/api/migration/start \
  -H "Content-Type: application/json" \
  -d '{
    "source_blob_url": "https://example.blob.core.windows.net/uploads/assets.csv",
    "source_system": "Maximo",
    "customer_id": "customer-123"
  }'

# Expected response:
# {
#   "migration_id": "550e8400-e29b-41d4-a716-446655440000",
#   "status": "pending",
#   "message": "Migration job created and queued for processing"
# }
```

### Check Migration Status
```bash
# Use migration_id from above
MIGRATION_ID="550e8400-e29b-41d4-a716-446655440000"

curl http://localhost:8003/api/migration/$MIGRATION_ID/status
# Expected: {"migration_id": "...", "status": "processing", ...}
```

### List Migrations
```bash
curl http://localhost:8003/api/migration/list
# Expected: {"migrations": [...]}
```

### WebSocket Real-Time Updates
```bash
# Using websocat or similar
websocat ws://localhost:8003/ws/migration/$MIGRATION_ID

# Or use curl with headers:
curl -i -N -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: SGVsbG8sIHdvcmxkIQ==" \
  http://localhost:8003/ws/migration/$MIGRATION_ID
```

---

## Step 6: Run Tests

### All Tests
```bash
docker compose exec svc-ai-schema-mapper pytest tests/ -v
```

### Specific Test Module
```bash
# REST API tests
docker compose exec svc-ai-schema-mapper pytest tests/test_api_endpoints.py -v

# E2E pipeline tests
docker compose exec svc-ai-schema-mapper pytest tests/test_e2e_pipeline.py -v

# WebSocket tests
docker compose exec svc-ai-schema-mapper pytest tests/test_websocket.py -v
```

### With Coverage Report
```bash
docker compose exec svc-ai-schema-mapper \
  pytest tests/ --cov=src --cov-report=html

# Copy to host
docker compose cp svc-ai-schema-mapper:/app/htmlcov ./coverage-report
```

---

## Step 7: View Observability

### Grafana Dashboards
```
URL: http://localhost:3000
Username: admin
Password: cafm-dev
```
Dashboards for:
- Service health
- API latency
- LangGraph node execution
- Database performance

### Prometheus Metrics
```
URL: http://localhost:9090
```
- Service metrics (requests, latency, errors)
- Database connection pool
- Redis memory usage

### Tempo Traces
```
URL: http://localhost:3200
```
- LangSmith trace integration
- Migration pipeline execution
- Node-by-node performance

---

## Common Operations

### View Logs
```bash
# Real-time logs
docker compose logs -f svc-ai-schema-mapper

# Last 50 lines
docker compose logs --tail 50

# With timestamps
docker compose logs --timestamps

# Since specific time
docker compose logs --since 5m
```

### Access Shell
```bash
docker compose exec svc-ai-schema-mapper bash

# Inside container:
python -m pytest tests/ -v
alembic upgrade head
curl http://localhost:8003/health
```

### Check Database
```bash
# Connect to database
docker compose exec postgres psql -U cafm -d cafm_connectors

# List tables
\dt plenum_cafm.*

# Query migrations
SELECT id, status, source_filename FROM plenum_cafm.migration_jobs;

# Exit
\q
```

### Restart Service
```bash
docker compose restart svc-ai-schema-mapper
```

### Stop All Services
```bash
# Keep volumes (data persists)
docker compose down

# Remove all volumes (clears data)
docker compose down -v
```

---

## Troubleshooting

### Service won't start
```bash
# Check logs
docker compose logs svc-ai-schema-mapper

# Check image was built
docker image ls | grep svc-ai

# Rebuild
docker compose build --no-cache svc-ai-schema-mapper
docker compose up -d svc-ai-schema-mapper
```

### "Port 8003 already in use"
```bash
# Find what's using port
lsof -i :8003

# Kill process
kill -9 <PID>

# Or change port in docker-compose.yml
# svc-ai-schema-mapper:
#   ports:
#     - "8004:8003"
```

### "Connection refused" errors
```bash
# Check services are healthy
docker compose ps

# Check logs for specific service
docker compose logs postgres
docker compose logs redis

# Verify network
docker network ls
docker network inspect cafm-connector-service-final_default
```

### API key not found in container
```bash
# Check .env file loaded
docker compose config | grep ANTHROPIC_API_KEY

# Check environment in container
docker compose exec svc-ai-schema-mapper env | grep API_KEY

# If missing, update .env and restart
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
docker compose restart svc-ai-schema-mapper
```

### Database migration failed
```bash
# Check migration status
docker compose logs postgres

# Run migrations manually
docker compose exec svc-ai-schema-mapper alembic upgrade head

# Check migration history
docker compose exec svc-ai-schema-mapper alembic history
```

---

## Performance Tuning

### For Development
```yaml
# docker-compose.override.yml
svc-ai-schema-mapper:
  command: uvicorn src.app:app --host 0.0.0.0 --port 8003 --reload
  environment:
    DEBUG: "true"
```

### For Production
```yaml
# docker-compose.yml
svc-ai-schema-mapper:
  command: uvicorn src.app:app --host 0.0.0.0 --port 8003 --workers 4
  restart: always
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8003/health"]
    interval: 10s
    timeout: 5s
    retries: 3
  deploy:
    resources:
      limits:
        cpus: '1.0'
        memory: 1G
```

---

## Next Steps

### Phase 9: Production Hardening
- [ ] Enable HTTPS/TLS
- [ ] Set up load balancing
- [ ] Configure monitoring alerts
- [ ] Set up backup strategy
- [ ] Document runbooks

### Scaling
- [ ] Horizontal scaling with Kubernetes
- [ ] Database replication
- [ ] Redis clustering
- [ ] Service mesh (Istio/Linkerd)

### CI/CD Integration
- [ ] GitHub Actions for automated testing
- [ ] Docker image registry (ECR/Dockerhub)
- [ ] Automated deployment pipeline
- [ ] Rollback procedures

---

## Support

### Documentation
- **API Specification:** `PROGRESS_REPORT.md`
- **Test Coverage:** `PHASE_8_TESTS.md`
- **Docker Reference:** `DOCKER_SETUP.md`
- **Quick Commands:** `DOCKER_QUICKREF.md`

### Endpoints
- **Health:** `GET http://localhost:8003/health`
- **Metrics:** `GET http://localhost:8003/metrics`
- **Docs:** Available in API specification

### Monitoring
- **Grafana:** http://localhost:3000
- **Prometheus:** http://localhost:9090
- **Tempo:** http://localhost:3200

---

## Checklist

- [ ] Docker and Docker Compose installed
- [ ] `.env` file created with API keys
- [ ] `./docker-build.sh` executed successfully
- [ ] All services show "running" in `docker compose ps`
- [ ] `curl http://localhost:8003/health` returns OK
- [ ] Tests pass: `docker compose exec svc-ai-schema-mapper pytest tests/ -v`
- [ ] Grafana accessible: http://localhost:3000
- [ ] Prometheus accessible: http://localhost:9090
- [ ] Test migration created and processed
- [ ] WebSocket connection verified

---

**Deployment Status:** ✅ READY  
**Last Updated:** 2026-04-02  
**Maintainer:** Plenum Tech
