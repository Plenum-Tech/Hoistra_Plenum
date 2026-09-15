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

# doc-rag: the document index. Every uploaded PDF goes through it, and it was missing from
# this image entirely — deep-agents dialled it, got nothing, and reported the upload as
# complete regardless. Its own Dockerfile runs it on 8000 with --root-path /doc-rag; 8000 is
# the connector here, so it takes 8004, which is the port deep-agents already looks for.
#
# In its own virtualenv, because it pins hard — fastapi==0.115.0, pydantic==2.9.2,
# sqlalchemy==2.0.35 — and installed into the shared site-packages those pins won for
# everybody: the first attempt at this took connector and schema-mapper down to 502, six
# services moved underneath by the seventh's requirements file. One image running seven
# services is a deployment convenience, not a claim that they share a dependency set.
RUN python -m venv /opt/docrag && /opt/docrag/bin/pip install --no-cache-dir --upgrade pip
COPY ${BE}/doc-rag-main/requirements.txt doc-rag/
RUN /opt/docrag/bin/pip install --no-cache-dir -r doc-rag/requirements.txt
COPY ${BE}/doc-rag-main/ doc-rag/

COPY --from=frontend /app/dist /usr/share/nginx/html
COPY deploy/allinone/nginx.conf       /etc/nginx/conf.d/hoistra.conf
COPY deploy/allinone/supervisord.conf /etc/supervisor/conf.d/hoistra.conf

# ── the deployment's own .env, carried into the image ────────────────────────────────
# The glob, not a bare `COPY .env`, because .env is gitignored: a clone that does not have
# one must still build. .env.example is committed, so the pattern always matches at least
# one file and the COPY cannot fail.
#
# Each service resolves env_file=".env" against its WORKING DIRECTORY, which supervisord
# sets per service (/app/svc-operations-intelligence and so on). A single /app/.env would
# therefore be read by deep-agents alone, which walks up from its own module looking for
# one — so the file is placed in every service directory that reads it.
#
# Two things this does NOT do, and both matter when reading a value back:
#
#   * It does not override the platform. pydantic-settings ranks real environment
#     variables above env_file, and the Container App sets 37 of them — ANTHROPIC_API_KEY,
#     DB_URL, REDIS_URL, AUTH_JWT_SECRET among them. Those still win. This fills gaps,
#     it does not replace the portal.
#   * It does not reach the frontend. VITE_* values are inlined by `npm run build` in the
#     stage above this one; a file copied here is read at runtime and cannot affect a
#     bundle that was already compiled. VITE_ORGANIZATION_ID has to be --build-arg.
#
# And one cost, stated plainly because it does not go away: whatever is in .env is now in
# an image layer. Anyone who can pull from the registry can read it, and it stays in every
# tag built this way. The DSN in this file carries the azure_pg_agentic_ai password, which
# is on the rotation list.
COPY .env* /app/
RUN set -e; \
    if [ -f /app/.env ]; then \
      for svc in svc-operations-intelligence svc-work-order-management \
                 svc-ai-schema-mapper svc-udr svc-deepagents doc-rag \
                 cafm-connector-service; do \
        if [ -d "/app/$svc" ]; then cp /app/.env "/app/$svc/.env"; fi; \
      done; \
      echo "allinone: .env carried into the image and placed per service"; \
    else \
      echo "allinone: no .env in the build context - platform env is the only source"; \
    fi

# Where the services find each other inside this container. Stated rather than inherited:
# every one of these is a default in some service's config, and a default that silently
# stops matching the ports supervisord starts is how a document upload comes to answer
# "Ingestion complete" while writing nothing.
ENV DOC_RAG_BASE_URL=http://127.0.0.1:8004 \
    MIGRATION_BASE_URL=http://127.0.0.1:8003 \
    UDR_BASE_URL=http://127.0.0.1:8006 \
    WO_MANAGEMENT_BASE_URL=http://127.0.0.1:8007 \
    OPERATIONS_INTELLIGENCE_BASE_URL=http://127.0.0.1:8009

# Where THIS deployment is actually reached from a browser — not another service inside
# the container, a person's mail client. Three settings build links that leave the container
# in an email, and all three default to a localhost that is dead the moment it is read
# anywhere but a developer's laptop:
#
#   PUBLIC_APP_URL       engines/auth/invitations.py  -> {url}/accept-invitation?token=...
#   FRONTEND_PUBLIC_URL  engines/compliance/{tokens,passport,certificates,scan}.py, and
#                        api/routes/compliance.py — every emailed link back into the UI
#   PUBLIC_BASE_URL      engines/compliance/tokens.py -> the one-click approval URL, which
#                        must reach the BACKEND, served here under /backend/ops-intelligence
#                        (deploy/allinone/nginx.conf:33)
#
# Only PUBLIC_APP_URL was set; the other two still emitted http://localhost:3000/... and
# http://localhost:8009/... to vendors and approvers. All three share one browser-facing
# origin, so they are stated together and move together.
#
# A Container Apps revision can override any of these at the platform level (environment
# variables in the portal/CLI take precedence over an image's own ENV at container start),
# so these are the production defaults, not the only place they can be set.
ENV PUBLIC_APP_URL=https://hoistra-frontend.gentlesmoke-fd81e91e.uaenorth.azurecontainerapps.io \
    FRONTEND_PUBLIC_URL=https://hoistra-frontend.gentlesmoke-fd81e91e.uaenorth.azurecontainerapps.io \
    PUBLIC_BASE_URL=https://hoistra-frontend.gentlesmoke-fd81e91e.uaenorth.azurecontainerapps.io/backend/ops-intelligence

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
