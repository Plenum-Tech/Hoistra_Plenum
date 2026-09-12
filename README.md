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
- `PATCH /api/compliance/certificates/{id}/remedial-status` — Escalate on a certificate row
- `POST /api/compliance/certificates/{id}/confirm` — confirm a draft certificate (from the row action or an offer the orchestrator's own answer put in front of you; `ccRunOffer`'s own comment flags both as real writes)

The response is reshaped in `apps/frontend/src/logic/complianceLive.js` into the same shape as the seed
dataset, so the console renders either. When the backend is unreachable the seed is shown and the page
says so (status pill next to "Run compliance scan", with Retry).

## Home — live data

The Home page reads `svc-operations-intelligence` the same way (`apps/frontend/src/logic/homeLive.js`,
wrappers in `src/api/opsIntelligence.js`):

- `GET /api/compliance/saved-space/summary`, `/api/contract-performance/contracts`, `/api/energy/meters` —
  the Hoist Score (ingestion coverage per source; asset registers stay unsourced until the connector runs)
- `GET /api/approvals`, `/api/energy/anomalies` — the Hoist Crons feed, newest first; rows open read-only
- the compliance register already loaded for the console — buildings, certificates and countries in the hero line
- the ask bar opens the conversation page (`apps/frontend/src/screens/Chat.jsx`, view `chat`): the question and
  the orchestrator's answer are the page, laid out like the Plenum AI shell's Orchestrator screen, with the
  connection line read from `GET /api/workflow/tools` on `svc-deepagents`. The compliance console keeps its
  side dock; both share one transcript.

The P&L tile stays on seed figures and says so: no backend holds a budget ledger. Each tile falls back
to the seed and labels itself when its source does not answer. `npm test` in `apps/frontend` runs the
shaping tests (Node 24).

## Vendors — live data

The Vendors page reads `svc-operations-intelligence` through `apps/frontend/src/logic/vendorsLive.js`
(wrappers in `src/api/opsIntelligence.js` and `src/api/compliance.js`). Reads only; nothing on the page
writes to the backend yet.

- `GET /api/contract-performance/saved-space/summary` — the newest 200 monthly scorecards (one card per
  vendor per month, the published `overall_score`, the engine's component points), the weights, and the
  count of pending Feature B approvals. `GET /api/contract-performance/scorecards` is the fallback.
- `GET /api/contract-performance/contracts` — contract parameter sets with `field_sources`; the Contract
  terms tab, the "terms read / on default" figures and the SLA hours behind the scorecard rows.
- `GET /api/contract-performance/admin/weights` — the weights the "Scoring weights" button reports.
- `GET /api/contract-performance/approvals` — pending Feature B items: `invoice_flag*` items become the
  Invoices tab rows (matched to a vendor by the contract reference in the invoice ref) and the "Invoice
  lines held" tile; high-severity items are "Pending critical".
- `GET /api/compliance/certificates?cert_scope=vendor`, `GET /api/compliance/coverage/vendors`,
  `GET /api/compliance/country-pack` — the Coverage tab, the directory's coverage column, the block state
  that drives "Vendors blocked" and the ceiling note.

The ask bar on the Vendors page (and on Buildings) goes to the orchestrator the way the compliance console's
does: the answer streams into the side dock beside the page, and `chatContext()` tells the orchestrator which
page the user is on and what it is showing (vendors and scores, the selected vendor and tab; or the building
table, the selected building and table in the Hoist Graph).

The directory is the union of vendors with a scorecard and vendors with a parameter set. Scores are never
recomputed: the card's `overall_score` is shown, and the rows beneath it are the engine's own points against
its weights snapshot (the published score blends 85% of that total with 15% of the invoice match signal,
which the scorecard note discloses).

Not sourced — no read endpoint exists — and therefore shown as "—" or left empty instead of seed figures:
the per-work-order breach list behind a score (Evidence tab; `vendor_wo_scores` is written but never
listed), the vendor's L1/L2/L3 job split, annual spend, contract expiry dates and page counts, matched
invoice lines (only flagged lines reach the queue), open work orders, and the "Last rebuild" time
(`created_at` is on the scorecard row but not in the list response). "Rebuild scorecards", "Claim
credits", "Raise credit note" and "Approve as charged" keep the scripted flow: the endpoints exist
(`POST /score/from-udr`, `POST /scorecards/monthly`, `POST /invoices/{id}/lines/decide`,
`POST /approvals/{id}/decide`) but they write to the production database and are not wired.

## Buildings — live data

The Buildings page is read from `apps/frontend/src/logic/buildingsLive.js` (the table),
`graphLive.js` and `buildingsGraph.js` (the graph panels below it); wrappers in
`src/api/energy.js`. Reads only; nothing on the page writes except Hoist / Edit / Remove,
described below.

- **Building table** — `GET /api/energy/buildings` on svc-operations-intelligence. Rooted on
  `plenum_cafm.buildings` once any row exists there; falls back to one row per
  `plenum_cafm.sites` row on a deployment that has not been backfilled into `buildings` yet
  (the response says which table is root). DB-only, no seed fallback.
- **Hoist Graph diagram** — a fixed set of hand-placed hub positions (`HUBS` in
  `constants.js`) filled with real buildings from the table already loaded (`glHubs`); a
  portfolio larger than the canvas is partly drawn and says so (`glHubNote`). Shared nodes are
  the regulation packs two or more drawn buildings are scored against, grouped from the rows
  themselves — vendor nodes were invented in the seed and are gone rather than kept, since
  nothing here says which vendor serves which building.
- **Per-building counts** — each row's own `graph_counts`: floors, assets, documents,
  contracts, equipment, meters, work orders, certificates, invoices. Three states, never two —
  a real count, a real zero, or "?" when nothing on the register has ever reported that branch
  (`glCount`/`glBranchCounted`).
- **The per-row drawer** (`buildingsGraph.js`) — a canonical tree (floors → spaces, assets →
  equipment/meters, documents → certificates, contracts → work orders/invoices) drawn
  immediately from the row's own counts, then `GET /api/energy/buildings/{id}/graph` fills in
  the real child rows, cached per building. Also reads
  `GET /api/energy/buildings/{id}/cost-drivers`, ranked on the gap over contract rather than on
  billed, with spend no work order attributes to any asset kept out of the ranking and shown
  separately.
- **Per-table counts and history** — `GET /api/energy/graph/tables` (`glLoadTables`), read once
  and cached; the export/schema view falls back to "not counted" rather than blocking on it.

**Hoist a building**, **Edit** and **Remove** are real writes against svc-operations-intelligence's Buildings
API (`POST` / `PATCH` / `DELETE /api/energy/buildings/{id}`), open to either role
(`apps/frontend/src/logic/buildingsCrud.js`, `HOIST_ROLES`) since adding or correcting a property a facilities
manager keeps is the work, not an administrative exception to it — the service does not yet authorise these
routes itself (the file's own comment says so; the real fix belongs there). Hoist runs as a three-step card in
the orchestrator dock (`HoistBuildingCard.jsx`): the record, the schema it landed in with the allocated code,
then documents. Edit is the same card as one step: opening it on a row prefills every field — including the
primary-use enum verbatim (`site_type`), not the resolved TM46 category `GET` also returns — and the submit
sends only the fields that changed, with `expected_updated_at` (read off the row) so a stale edit is refused
with a 409 rather than overwritten. Remove calls `DELETE` without `confirm` first, which changes nothing and
reports what the building holds; that report is the whole confirmation dialog. A country change re-resolves
the building's location, which is what actually carries its regulation pack.

Not sourced, and shown as such: vector-similarity badges and document file sizes (no service this panel
reaches holds either), and which vendor serves which building on the diagram.

## Ask bars — orchestrator

The ask bar on the compliance console, the Vendors page and the Buildings page goes to the orchestrator
(`svc-deepagents`) and the answer streams into the side dock beside the page
(`DOCK_VIEWS = ["cc","vp","buildings"]` in `apps/frontend/src/logic/complianceLive.js`), so the tiles and
tables stay in view. Home is deliberately not a dock view — its ask bar opens the full chat page instead, same
as reopening a conversation from the sessions list — and all three surfaces share one transcript. A dock
carried onto another page with a task in progress stays open there and questions are answered beside it;
asking never discards what was in progress — Cancel does (`dockAnswers()`). A reload on a dock page reopens
the dock on the restored transcript. `chatContext()` tells the
orchestrator which page the user is on and what it is showing: the register scope on compliance; the vendors
and their scores, the selected vendor and tab on Vendors; the site count, the selected building, the selected
table and the child tables of the Hoist Graph on Buildings.

## Sessions, spaces and custom reports

The navigator follows the Plenum AI shell's model (`apps/frontend/src/logic/sessions.js`, `spacesLive.js`,
`reports.js`; details in the frontend README):

- **Sessions** are the orchestrator threads: one record per `session_id` with the transcript, the page it was
  asked from and the engine that answered. svc-deepagents lists no threads, so the list lives in the browser
  (`localStorage`), as it does in the Plenum shell; reopening a session continues the same server thread.
- **Spaces** are the four engines with live badges from `GET /api/compliance/saved-space/summary`,
  `/api/energy/anomalies`, `/api/contract-performance/saved-space/summary` and `/api/approvals`, plus the
  saved spaces in `plenum_cafm.saved_spaces` through svc-udr's `GET/POST/PATCH/DELETE /api/spaces` — the one
  navigator action that writes, on the user's click.
- **Custom reports** pin a session's question and re-run it on a cadence through
  `POST /api/workflow/run-stateful` (fresh `report-…` thread per refresh), keeping the last three answers.
  `plenum_cafm.pinned_run` has no route, so reports live in the browser and re-run while the app is open.

## Notes

- Energy and Integrations still run on the seed data in `apps/frontend/src/data/`; they are the next
  candidates for the same treatment. The "Pending" pill and the decision queue are still seed — the
  approvals queue the Home feed reads is the obvious source for them. The navigator badges now read the
  spaces' live figures.
- Original source repos remain unchanged; this monorepo is a copy-based consolidation.
