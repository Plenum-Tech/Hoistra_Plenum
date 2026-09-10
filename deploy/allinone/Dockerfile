# The whole platform in one image: the frontend, six backend services, and the nginx that
# ties them together on port 80.
#
#   docker build -f deploy/allinone/Dockerfile -t hoistra-platform:dev .
#
# Build context is the REPOSITORY ROOT, because this image needs both apps/frontend and
# apps/backend and no directory below them contains the pair.
#
# ── why this file exists ─────────────────────────────────────────────────────────────
#
# The image running as the `cafm` container app serves exactly this shape — a frontend at
# / and services under /backend/* on port 80 — but nothing in this repository builds it.
# apps/backend/cafm-connector-service-final/Dockerfile is the nearest thing and does not
# match: it routes /connector/ rather than /backend/connector/, and it has no
# ops-intelligence, no deep-agents, no udr and no frontend. Whatever produced the deployed
# image was assembled somewhere else. This file makes it reproducible from source.
#
# ── one image or several ─────────────────────────────────────────────────────────────
#
# docker-compose.testenv.yml runs the same code as eight containers, which is the better
# shape: each service scales, fails and deploys on its own. This image trades that away
# for one deployable unit on one ingress — the arrangement the Container App already has.
# Both are built from the same source, so neither is a fork.

# ── the frontend ─────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS frontend
WORKDIR /app
COPY apps/frontend/package.json apps/frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY apps/frontend/ ./
# Relative paths, deliberately. The frontend and the API share an origin here, so
# /backend/ops-intelligence resolves against whatever host serves the page — localhost, a
# Container Apps FQDN, a tunnel — with nothing baked in that has to be rebuilt per
# environment. Absolute URLs would pin this image to one deployment.
ARG VITE_OPS_INTELLIGENCE_BASE_URL=/backend/ops-intelligence
ARG VITE_WO_BASE_URL=/backend/work-order
ARG VITE_CONNECTOR_BASE_URL=/backend/connector
ARG VITE_SCHEMA_MAPPER_BASE_URL=/backend/schema-mapper
ARG VITE_DEEP_AGENTS_BASE_URL=/backend/deep-agents
ARG VITE_ORGANIZATION_ID=""
ENV VITE_OPS_INTELLIGENCE_BASE_URL=$VITE_OPS_INTELLIGENCE_BASE_URL \
    VITE_WO_BASE_URL=$VITE_WO_BASE_URL \
    VITE_CONNECTOR_BASE_URL=$VITE_CONNECTOR_BASE_URL \
    VITE_SCHEMA_MAPPER_BASE_URL=$VITE_SCHEMA_MAPPER_BASE_URL \
    VITE_DEEP_AGENTS_BASE_URL=$VITE_DEEP_AGENTS_BASE_URL \
    VITE_ORGANIZATION_ID=$VITE_ORGANIZATION_ID
RUN npm run build

# ── the platform ─────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        nginx supervisor curl gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/log/supervisor \
    && rm -f /etc/nginx/sites-enabled/default

ARG BE=apps/backend/cafm-connector-service-final
WORKDIR /app

# The connector's ORM is the authoritative model layer — every other service imports it,
# so it goes first and its failure is the one worth seeing first.
COPY ${BE}/cafm-connector-service/pyproject.toml cafm-connector-service/
COPY ${BE}/cafm-connector-service/src            cafm-connector-service/src/
RUN pip install --no-cache-dir -e "cafm-connector-service[postgresql,files,vault,azure]"

COPY ${BE}/shared-lib shared-lib/
RUN pip install --no-cache-dir -e "shared-lib"

# Requirements before source, in every case: the dependency layer then survives a code
# change, and a rebuild after editing a route costs seconds rather than minutes.
COPY ${BE}/svc-operations-intelligence/requirements.txt svc-operations-intelligence/
RUN pip install --no-cache-dir -r svc-operations-intelligence/requirements.txt
COPY ${BE}/svc-operations-intelligence/ svc-operations-intelligence/

COPY ${BE}/svc-work-order-management/requirements.txt svc-work-order-management/
RUN pip install --no-cache-dir -r svc-work-order-management/requirements.txt
COPY ${BE}/svc-work-order-management/ svc-work-order-management/

COPY ${BE}/svc-ai-schema-mapper/pyproject.toml svc-ai-schema-mapper/
RUN pip install --no-cache-dir -e "svc-ai-schema-mapper"
COPY ${BE}/svc-ai-schema-mapper/ svc-ai-schema-mapper/

COPY ${BE}/svc-udr/pyproject.toml svc-udr/
RUN pip install --no-cache-dir -e "svc-udr"
COPY ${BE}/svc-udr/ svc-udr/

COPY ${BE}/svc-deepagents/pyproject.toml svc-deepagents/
RUN pip install --no-cache-dir -e "svc-deepagents"
COPY ${BE}/svc-deepagents/ svc-deepagents/

COPY --from=frontend /app/dist /usr/share/nginx/html
COPY deploy/allinone/nginx.conf       /etc/nginx/conf.d/hoistra.conf
COPY deploy/allinone/supervisord.conf /etc/supervisor/conf.d/hoistra.conf

# Where the services find each other inside this container. Every other default already
# matches the ports supervisord starts — 8000 connector, 8003 schema-mapper, 8006 udr,
# 8007 work-order, 8009 ops-intelligence — but doc-rag defaults to :8004, and nothing in
# this image listens there: doc-rag is served by the schema-mapper process on 8003.
#
# Leaving it wrong produces no error anyone sees. A document upload answers 200 with
# "Ingestion complete", the log carries one ConnectError, no row is created, and the file
# is silently gone. Worth an ENV line.
ENV DOC_RAG_BASE_URL=http://127.0.0.1:8003

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OTEL_SDK_DISABLED=true

EXPOSE 80

# nginx answers this as soon as it is up, which is what the ingress needs to start routing.
# It says the front door is open, not that all six services are ready — those report their
# own health on /backend/<service>/health.
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
    CMD curl -fsS http://127.0.0.1/healthz >/dev/null || exit 1

CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
