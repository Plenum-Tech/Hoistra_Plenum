---
name: architecture-caveats
description: Docs-vs-code drift and major wiring gaps — verify before trusting any requirements doc
metadata:
  type: project
---

The repo's `docs/REQUIREMENTS.md`, `CHANGE_SPEC.md`, and per-service `CLAUDE.md` files are **aspirational** and drift from the code. Verified discrepancies (2026-06-12):

**Wiring gaps (built but not invoked):**
- **svc-query (Layer 7) has NO query API** — `app.py` only mounts `/health` + `/metrics`. The 3 tiers, document generator, EL-7 eval are implemented + unit-tested but unreachable over HTTP and imported nowhere.
- **svc-ingestion Layer 5/6 never invoked** — all 5 `run_*_agent` + `run_orchestration` have no callers. The determinism cycle (Bound→Aggregate N=3→Vote→Constrain) is fully built but dead.
- **svc-ingestion worker `extract_document` is still a stub** (logs "not yet implemented"); real extraction only runs via synchronous `/ingest/*` HTTP endpoints. XML/JSON agent fully built but has no endpoint.
- **DeepAgents HITL is half-built** — AsyncPostgresSaver + resume() + gate_interrupt WS event all exist, but NO tool calls LangGraph `interrupt()`. Migration HITL is conversational (poll → return gate payload → submit_* tool), not interrupt-based.

**Count/naming drift:**
- DeepAgents registers **80 tools / 9 domains**, not 46/6. All docstrings, system prompt, `/tools` route disagree (say 38/45/46/54).
- `start_migration_multi` IS implemented and used by `single_door_flow.py`, but is NOT in the orchestrator's `ALL_TOOLS` — prompt tells the LLM to call a tool it doesn't have.
- Two LangGraphs in schema-mapper: `migration_graph.py` (10 nodes, 4 gates) + `schema_mapping_graph.py` (12 nodes). Docs describe a single 9-node `graph/graph.py` that doesn't exist.

**WO engine 15-step pipeline:** only step 3 (criticality) uses an LLM (with deterministic fallback); steps 6–12 are hardcoded/faked demo data (spare parts always "5 in Main Store", fixed vendor/technician lists). Two parallel assessment engines exist (15-step `IntelligentWorkOrderEngine` vs 13-block `AIExtractionService` used by email). Runtime bug: `settings.outlook_access_token` referenced but never defined in config → PPM worker email path crashes.

**Frontend demo-vs-live split:** every CRUD domain has BOTH a seeded in-memory mock (`src/app/api/<domain>/route.ts` + server `actions.ts`) AND a live client path (`<domain>/plenum-api.ts` → real `/api/v1/plenum/...`). Reads and writes can target different systems on the same page. Auth is a stub (accepts any creds, token = base64 email, middleware is a no-op). The `orchestrator/` dir is empty — real code is in `pipeline/deep-agent/`.

**B-5 bug:** schema-mapper pre-semantic gate supports table rename + new-table + column data-type; the field-mapping gate only supports new columns via constrained `custom` DDL (no table rename / column rename). Backend capability is asymmetric across the two gates, not uniformly missing.

[[project-overview]] [[security-committed-secrets]]
