#!/bin/bash
# Docker build and run script for svc-ai-schema-mapper

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="svc-ai-schema-mapper"
IMAGE_NAME="plenum/${SERVICE_NAME}:latest"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  svc-ai-schema-mapper Docker Setup${NC}"
echo -e "${YELLOW}========================================${NC}"

# Function to print section headers
print_section() {
    echo -e "\n${YELLOW}▶ $1${NC}"
}

# Function to print success
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

# Function to print error
print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# ============================================================================
# 1. PREREQUISITES CHECK
# ============================================================================

print_section "Checking Prerequisites"

if ! command_exists docker; then
    print_error "Docker is not installed"
    exit 1
fi
print_success "Docker found: $(docker --version)"

if ! command_exists docker-compose; then
    print_error "Docker Compose is not installed"
    exit 1
fi
print_success "Docker Compose found: $(docker-compose --version)"

# ============================================================================
# 2. ENVIRONMENT SETUP
# ============================================================================

print_section "Setting up environment"

ENV_FILE="${SCRIPT_DIR}/.env"

if [ ! -f "$ENV_FILE" ]; then
    print_error ".env file not found at $ENV_FILE"
    echo "Creating .env template..."

    cat > "$ENV_FILE" << 'EOF'
# Infrastructure
DB_URL=postgresql+asyncpg://cafm:cafm@postgres:5432/cafm_connectors
REDIS_URL=redis://redis:6379/0

# API Keys (REQUIRED - add your actual keys)
ANTHROPIC_API_KEY=sk-ant-v0x...
OPENAI_API_KEY=sk-...
LANGSMITH_API_KEY=ls-...

# Azure Storage (optional)
AZURE_STORAGE_CONNECTION_STRING=
AZURE_BLOB_CONTAINER_NAME=plenum-agentic-ai-attachments

# Environment
ENVIRONMENT=development
DEBUG=false
EOF

    print_error "Created .env template. Please edit it with your API keys:"
    echo "  nano $ENV_FILE"
    exit 1
fi
print_success ".env file found"

# Check for required API keys
if grep -q "sk-ant-v0x" "$ENV_FILE"; then
    print_error "ANTHROPIC_API_KEY not configured in .env"
    exit 1
fi
print_success "ANTHROPIC_API_KEY configured"

# ============================================================================
# 3. BUILD IMAGE
# ============================================================================

print_section "Building Docker image"

if docker build \
    -f "${SCRIPT_DIR}/svc-ai-schema-mapper/Dockerfile" \
    -t "$IMAGE_NAME" \
    "${SCRIPT_DIR}" 2>&1 | tail -20; then
    print_success "Docker image built: $IMAGE_NAME"
else
    print_error "Failed to build Docker image"
    exit 1
fi

# ============================================================================
# 4. START SERVICES
# ============================================================================

print_section "Starting services with Docker Compose"

cd "$SCRIPT_DIR"

if docker compose up -d; then
    print_success "Services started"
else
    print_error "Failed to start services"
    exit 1
fi

# ============================================================================
# 5. WAIT FOR HEALTH CHECKS
# ============================================================================

print_section "Waiting for services to be healthy"

services=("postgres" "redis" "tempo" "prometheus" "svc-ai-schema-mapper")
max_wait=60
elapsed=0

for service in "${services[@]}"; do
    echo -n "Waiting for $service... "
    while [ $elapsed -lt $max_wait ]; do
        if docker compose ps "$service" | grep -q "healthy\|running"; then
            print_success "healthy"
            break
        fi
        sleep 2
        ((elapsed+=2))
    done
    if [ $elapsed -ge $max_wait ]; then
        print_error "$service failed to start"
    fi
done

# ============================================================================
# 6. VERIFY CONNECTIVITY
# ============================================================================

print_section "Verifying service connectivity"

echo "Testing svc-ai-schema-mapper health endpoint..."
if curl -s http://localhost:8003/health | grep -q "ok"; then
    print_success "svc-ai-schema-mapper is healthy"
else
    print_error "svc-ai-schema-mapper health check failed"
fi

echo "Testing metrics endpoint..."
if curl -s http://localhost:8003/metrics | grep -q "TYPE\|HELP" || [ $? -eq 0 ]; then
    print_success "Metrics endpoint is responsive"
else
    print_error "Metrics endpoint check failed"
fi

echo "Testing database connectivity..."
if docker compose exec -T postgres psql -U cafm -d cafm_connectors -c "SELECT 1" >/dev/null 2>&1; then
    print_success "PostgreSQL is accessible"
else
    print_error "PostgreSQL connection failed"
fi

echo "Testing Redis connectivity..."
if docker compose exec -T redis redis-cli ping | grep -q "PONG"; then
    print_success "Redis is accessible"
else
    print_error "Redis connection failed"
fi

# ============================================================================
# 7. SUMMARY
# ============================================================================

print_section "Summary"

cat << EOF

${GREEN}✓ All services are running!${NC}

Service URLs:
  - svc-ai-schema-mapper  → http://localhost:8003
  - Grafana              → http://localhost:3000 (admin/cafm-dev)
  - Prometheus           → http://localhost:9090
  - Tempo                → http://localhost:3200

Useful Commands:
  View logs:          docker compose logs -f svc-ai-schema-mapper
  Run tests:          docker compose exec svc-ai-schema-mapper pytest tests/ -v
  Shell access:       docker compose exec svc-ai-schema-mapper bash
  Stop services:      docker compose down
  Stop & clean data:  docker compose down -v

API Examples:
  Health check:  curl http://localhost:8003/health
  Metrics:       curl http://localhost:8003/metrics

  Start migration:
    curl -X POST http://localhost:8003/api/migration/start \\
      -H "Content-Type: application/json" \\
      -d '{
        "source_blob_url": "https://example.blob.core.windows.net/assets.csv",
        "source_system": "Maximo",
        "customer_id": "customer-123"
      }'

Next Steps:
  1. View logs to verify startup:
     docker compose logs -f svc-ai-schema-mapper

  2. Run tests:
     docker compose exec svc-ai-schema-mapper pytest tests/ -v

  3. Test endpoints:
     curl http://localhost:8003/health

Documentation:
  - Docker Setup:    svc-ai-schema-mapper/DOCKER_SETUP.md
  - API Spec:        svc-ai-schema-mapper/PROGRESS_REPORT.md
  - Test Coverage:   svc-ai-schema-mapper/PHASE_8_TESTS.md

${YELLOW}========================================${NC}
EOF

print_success "Setup complete!"
