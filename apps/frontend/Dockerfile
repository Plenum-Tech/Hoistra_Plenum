# Hoistra frontend — React 18 + Vite, built to static files and served by nginx on :3000.
# The single-URL gateway (infra/single-url/nginx.conf) proxies "/" to http://frontend-app:3000.
#
# Backend paths are baked in at build time. Defaults are the same-origin gateway paths, so a
# plain `docker build` produces an image that works behind the gateway with no arguments.

FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY . .

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

FROM nginx:1.27-alpine AS runner
COPY infra/frontend.nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1:3000/healthz >/dev/null || exit 1
