# Hoistra — CMMS monorepo

Frontend and backend in one workspace so deployment, routing and local testing are managed together.

## Layout

- `apps/frontend` — Hoistra UI, React 18 + Vite (see its README for the store / screen pattern)
- `apps/backend/cafm-connector-service-final` — the Python FastAPI services (connector, work-order, schema-mapper, UDR, deep-agents, operations-intelligence)
- `apps/frontend/infra/single-url` — nginx gateway config + image for the single-URL model
- `infra`, `specs`, `memory` — shared infra assets, feature specs, project notes

## Single URL model

Everything is served from one origin by the gateway (`http://localhost:3000` locally):

| Path | Service |
| --- | --- |
| `/` | frontend (nginx serving the Vite build, container port 3000) |
| `/backend/ops-intelligence/` | svc-operations-intelligence :8009 — compliance, contract performance, energy |
| `/backend/work-order/` | svc-work-order-management :8007 |
| `/backend/schema-mapper/`, `/backend/doc-rag/` | svc-ai-schema-mapper :8003 |
| `/backend/connector/` | cafm-connector-service :8000 |
| `/backend/udr/` | svc-udr :8006 |
| `/backend/deep-agents/` | svc-deepagents :8008 |

The frontend calls the backends by these same-origin paths (`apps/frontend/src/api/client.js`), so no CORS
configuration is needed. The paths are baked in at build time via `VITE_*` build args.

## Local run

1. Secrets are never committed. Create the two env files from their examples and fill in the values:

   ```bash
   cp .env.example .env
   cp apps/backend/.env.example apps/backend/.env
   ```

   Root `.env` feeds `${VAR}` interpolation in the compose file (DB DSN, feature flags).
   `apps/backend/.env` is the container env_file for the Python services (API keys, SMTP, Graph).

2. Build and start:

   ```bash
   docker compose -f docker-compose.single-url.local.yml up --build
   ```

3. Open:

   - `http://localhost:3000/` — the UI
   - `http://localhost:3000/backend/ops-intelligence/health` — compliance engine health
   - `http://localhost:3000/backend/work-order/health`

## Frontend dev loop

Run the backends in Docker and the UI with hot reload:

```bash
docker compose -f docker-compose.single-url.local.yml up -d --build
cd apps/frontend && npm install && npm run dev
```

Vite serves on `http://localhost:5173` and proxies `/backend/*` to the gateway on `:3000`
(`VITE_DEV_PROXY_TARGET` overrides the target).

## Compliance console — live data

The Compliance screen reads `svc-operations-intelligence`:

- `GET /api/compliance/certificates` — every building and vendor certificate
- `GET /api/compliance/coverage/buildings`, `/coverage/vendors` — CountryPack coverage per building / vendor
- `GET /api/compliance/country-pack` — type names for the codes on certificates
- `POST /api/compliance/scan` — "Run compliance scan"
- `POST /api/compliance/certificates/{id}/verify`, `POST /api/compliance/certificates/renewal-email` — per-certificate actions

The response is reshaped in `apps/frontend/src/logic/complianceLive.js` into the same shape as the seed
dataset, so the console renders either. When the backend is unreachable the seed is shown and the page
says so (status pill next to "Run compliance scan", with Retry).

## Notes

- The other screens (Home, Energy, Vendors, Buildings, Integrations) still run on the seed data in
  `apps/frontend/src/data/`; they are the next candidates for the same treatment.
- Original source repos remain unchanged; this monorepo is a copy-based consolidation.
