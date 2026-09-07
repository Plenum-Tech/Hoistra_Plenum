# CAFM AI Platform — Project Context for Claude Code

> Read this file fully before writing any code, suggesting any architecture,
> or answering any question about this project. Everything here is authoritative.
> Last updated: April 2026

---

build new apps.
1

## 1. What we are building

An **AI-powered CAFM (Computer-Aided Facilities Management) platform** for
facilities management operations in the UAE. The platform has two major pillars:

### Pillar 1 — Structured Data Ingestion (EXISTS: cafm-connector-service)
Universal data connector that ingests structured data from 12 source types
(databases, files, APIs) into the PostgreSQL `plenum_cafm` schema.
**This service is production-ready. Do not rewrite it. Only extend it.**

### Pillar 2 — Multi-Agent AI Ingestion + Query + Document Generation Platform (Sprint 2 — IN PROGRESS)
A multi-agent ingestion system where every document type (PDF, Excel, Word,
CSV, XML/JSON) has its own specialised agent for extraction. All agents share
the same 4-stage pipeline and converge into one unified data store. Specialist
data agents then query that unified store — each with their own determinism
cycle — before feeding a Layer 6 orchestration analysis that produces
deterministic, auditable output. A document generation capability allows users
to request structured reports, schedules, templates, and operational documents
that are planned by Claude but rendered deterministically from real DB data.

---

## 1a. Evaluation-First Principle (read this before everything else)

**Every output produced by every agent at every layer is evaluated before it
advances to the next stage. No exceptions.**

This is not a post-processing step. It is the core quality gate of the entire
platform. The evaluation layer sits at every output boundary — ingestion agent
output, schema mapper output, every tier of entity resolution output, every
data agent output, Layer 6 output, and every generated document output.

```
EVALUATION LAYER PLACEMENT — COMPLETE MAP

Layer 2  Ingestion Agents
  ├── EL-2.0  File/document pre-validation (before agent runs)
  ├── EL-2.1  Raw extraction output (Claude response → intermediate JSON)
  ├── EL-2.2  Intermediate JSON schema conformance (Pydantic + rules)
  └── EL-2.3  LLM-as-judge eval_score (Haiku reviews extraction vs source)

Layer 3  Schema Mapper
  └── EL-3.0  Mapping confidence check (< 0.80 → human review before proceed)

Layer 4  Unified Store Write
  └── EL-4.0  Pre-write validation (Pydantic, FK checks, no null PKs)

Layer 5  Specialist Data Agents (per agent, per run)
  ├── EL-5.BOUND    Row-level Pydantic validation before AI sees data
  ├── EL-5.AGG      Per-run output validation (schema + value range checks)
  ├── EL-5.VOTE     Majority vote integrity check
  └── EL-5.CONSTRAIN  Hard rules + confidence gate + requires_human_review

Entity Resolver (called by Layer 2 and Layer 5)
  ├── EL-ER.T1   Tier 1 exact match output validation
  ├── EL-ER.T2   Tier 2 fuzzy match score + gap validation
  ├── EL-ER.T3   Tier 3 Claude re-query response validation
  └── EL-ER.T4   Tier 4 manual resolution submission validation

Layer 6  Orchestration
  ├── EL-6.BOUND    All 5 AgentResults present, typed, no human_review flags
  ├── EL-6.AGG      Per-run CMSDecision output validation
  ├── EL-6.VOTE     Majority vote integrity
  └── EL-6.CONSTRAIN  Confidence gate (≥ 0.85) + safety rules + audit write

Layer 7  Outputs
  ├── EL-7.QUERY    SQL-grounded answer: every value traces to a DB row
  ├── EL-7.DOC.PLAN DocumentPlan Pydantic validation + data source resolution
  ├── EL-7.DOC.RENDER  Post-render: 10 random value spot-checks vs DB
  ├── EL-7.DOC.EVAL  eval_score < 0.85 → held for review, never auto-delivered
  └── EL-7.TEMPLATE  All {{placeholders}} resolved before fill, verified after
```

The file `svc-ingestion/src/shared/eval_layer.py` implements EL-2.x and EL-5.x.
The file `svc-query/src/eval_layer.py` implements EL-7.x.
Entity resolver eval is inline in `shared/entity_resolver.py`.
Layer 6 eval is inline in `analysis/orchestrator.py`.

---

## 2. The 7-layer architecture

```
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — DATA SOURCES                                                  ║
║  CSV files (latin1) · Word/PDF documents · Direct DB connection          ║
║                                                                          ║
║  Known CSV files: assets · parts · work_orders · scheduled_pm            ║
║                   task_groups · users                                    ║
║  Known documents: 7-section site inspection report (Sections A–G)        ║
╚══════════════════════════════╤═══════════════════════════════════════════╝
                               ↓
                    ┌──────────────────────┐
                    │  EL-2.0              │
                    │  File pre-validation │
                    │  type · size · hash  │
                    └──────────┬───────────┘
                               ↓
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 2 — INGESTION AGENTS  (svc-ingestion)                             ║
║  One agent per source type — each produces Intermediate JSON             ║
║                                                                          ║
║  CSV Agent · DOCX Agent · PDF Agent · DB Agent · Excel Agent             ║
║  XML/JSON Agent                                                          ║
╚══════════════════════════════╤═══════════════════════════════════════════╝
                               ↓
                    ┌──────────────────────────────────────┐
                    │  EL-2.1  Raw extraction output eval  │
                    │  EL-2.2  Intermediate JSON schema    │
                    │  EL-2.3  LLM-as-judge eval_score     │
                    │  PASS → Layer 3  FAIL → review queue │
                    └──────────────────┬───────────────────┘
                               ↓
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 3 — AI SCHEMA MAPPER  (shared/schema_mapper.py)                   ║
║  Claude Haiku maps any customer column names → canonical field registry  ║
║  Unmatched columns → raw_metadata JSONB (never dropped)                  ║
║  Called once per new source — result cached in Redis                     ║
╚══════════════════════════════╤═══════════════════════════════════════════╝
                               ↓
                    ┌──────────────────────────────────────┐
                    │  EL-3.0  Mapping confidence check    │
                    │  < 0.80 → human review before write  │
                    └──────────────────┬───────────────────┘
                               ↓
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 4 — UNIFIED POSTGRESQL STORE  (plenum_cafm schema)                ║
║                                                                          ║
║  assets · work_orders · parts · scheduled_pm · inspections               ║
║  + 50 ORM model classes (29 core + 14 Fiix + 7 additional)               ║
║  + 9 new ingestion tables                                                ║
║  + Azure Blob (all original files preserved)                             ║
║  + pgvector (manuals/SOPs — Tier 3 queries only)                         ║
╚══════════════════════════════╤═══════════════════════════════════════════╝
                               ↓
                    ┌──────────────────────────────────────┐
                    │  EL-4.0  Pre-write validation        │
                    │  Pydantic · FK checks · no null PKs  │
                    └──────────────────┬───────────────────┘
                               ↓
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 5 — SPECIALIST DATA AGENTS  (each owns one domain)                ║
║                                                                          ║
║  Each agent runs its own DETERMINISM CYCLE with eval at every step:      ║
║  BOUND (EL-5.BOUND: validate SQL rows) →                                 ║
║  AGGREGATE (EL-5.AGG: validate each run output) →                        ║
║  VOTE (EL-5.VOTE: majority vote integrity) →                             ║
║  CONSTRAIN (EL-5.CONSTRAIN: confidence gate + hard rules + audit log)    ║
║                                                                          ║
║  Asset Agent · WO Agent · PM Agent · Parts Agent · Inspection Agent      ║
╚══════════════════════════════╤═══════════════════════════════════════════╝
                               ↓
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 6 — ORCHESTRATION ANALYSIS  (Bound → Aggregate → Constrain)       ║
║                                                                          ║
║  EL-6.BOUND: All 5 AgentResults present, typed, no human_review flags    ║
║  Receives 5 pre-validated AgentResult objects                            ║
║  EL-6.AGG: N=3 Sonnet runs · each run output validated                   ║
║  EL-6.VOTE: majority vote on action field                                ║
║  EL-6.CONSTRAIN: confidence gate ≥ 0.85 · safety rules · audit log       ║
║  action: create_wo | order_part | alert_critical | no_action             ║
╚══════════════════════════════╤═══════════════════════════════════════════╝
                               ↓
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 7 — DETERMINISTIC OUTPUT  (svc-query)                             ║
║                                                                          ║
║  A: Query answers  — EL-7.QUERY: SQL-grounded · every value to DB row    ║
║  B: Document fill  — EL-7.TEMPLATE: all placeholders resolved + verified ║
║  C: Document gen   — EL-7.DOC.PLAN + EL-7.DOC.RENDER + EL-7.DOC.EVAL     ║
║                      plan validated → render deterministic → eval ≥ 0.85 ║
║                                                                          ║
║  Output formats: DOCX · XLSX · PDF · JSON · text                         ║
║  Every value traces to a real DB row — EL-7.DOC.EVAL verifies this       ║
║  Audit receipt on every output                                           ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## 3. Repository structure

```
cafm-platform/
├── CLAUDE.md                              ← this file (always read first)
├── docker-compose.yml                     ← orchestrates ALL services
│
├── cafm-connector-service/                ← EXISTING — do not restructure
│   ├── src/cafm_connector/
│   │   ├── api/
│   │   │   ├── app.py
│   │   │   ├── dependencies.py
│   │   │   ├── routes/
│   │   │   │   ├── connectors.py
│   │   │   │   └── plenum_cafm/
│   │   │   │       ├── assets.py
│   │   │   │       ├── work_orders.py
│   │   │   │       ├── maintenance_vendors.py
│   │   │   │       ├── inventory_notifications.py
│   │   │   │       ├── org_users.py
│   │   │   │       ├── rbac.py
│   │   │   │       └── table_customizer.py   ← Table Editor FastAPI sub-app (see section 7b)
│   │   │   ├── schemas/
│   │   │   │   ├── connectors.py
│   │   │   │   └── plenum_cafm.py
│   │   │   └── websocket.py
│   │   ├── connectors/
│   │   │   ├── base.py
│   │   │   ├── registry.py
│   │   │   └── plugins/               ← 12 connector implementations
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── types.py
│   │   │   ├── exceptions.py          ← CAFMError hierarchy (reuse everywhere)
│   │   │   └── logging.py             ← structlog setup (reuse everywhere)
│   │   ├── jobs/
│   │   │   └── worker.py
│   │   ├── models/
│   │   │   ├── db.py
│   │   │   └── plenum_cafm.py         ← 50 CAFM ORM models (29 core + 14 Fiix expansion + 7 additional)
│   │   ├── secrets/
│   │   │   └── backend.py
│   │   └── services/
│   │       └── connector_service.py
│   └── pyproject.toml
│
├── svc-ingestion/                         ← NEW — Sprint 2
│   ├── src/
│   │   ├── app.py
│   │   ├── config.py
│   │   ├── worker.py                      ← ARQ worker stub (Phase 2 agents plug in)
│   │   │
│   │   ├── shared/                        ← Shared pipeline — built in Phase 1
│   │   │   ├── base_agent.py              ← Abstract BaseAgent
│   │   │   ├── intermediate_schema.py     ← ✅ BUILT — 12 Pydantic models, 39 tests
│   │   │   ├── db.py                      ← ✅ BUILT — async session factory
│   │   │   ├── ingest.py                  ← ✅ BUILT — Stage 1
│   │   │   ├── schema_mapper.py           ← NEW — Layer 3 canonical field mapping
│   │   │   ├── eval_layer.py              ← EL-2.1, EL-2.2, EL-2.3, EL-5.x (all ingestion + agent eval)
│   │   │   ├── confidence_router.py       ← reads eval_score → routes to accept/queue/re-extract
│   │   │   ├── unifier.py                 ← ✅ BUILT — Stage 4
│   │   │   ├── audit.py                   ← ✅ BUILT — pipeline audit receipts
│   │   │   ├── entity_resolver.py         ← 4-tier resolution + EL-ER.T1 through EL-ER.T4
│   │   │   └── agent_determinism.py       ← Bound→Aggregate→Constrain + EL-5.BOUND/AGG/VOTE/CONSTRAIN
│   │   │
│   │   ├── agents/                        ← Layer 2: ingestion agents
│   │   │   ├── pdf_agent.py               ← calls eval_layer after extraction (EL-2.1/2.2/2.3)
│   │   │   ├── excel_agent.py             ← calls eval_layer after extraction
│   │   │   ├── word_agent.py              ← calls eval_layer after extraction
│   │   │   ├── csv_agent.py               ← calls eval_layer after schema mapping
│   │   │   └── xml_json_agent.py          ← calls eval_layer after extraction
│   │   │
│   │   ├── data_agents/                   ← NEW — Layer 5: specialist query agents
│   │   │   ├── base_data_agent.py         ← Abstract — enforces full determinism + eval cycle
│   │   │   ├── asset_agent.py
│   │   │   ├── wo_agent.py
│   │   │   ├── pm_agent.py
│   │   │   ├── parts_agent.py
│   │   │   ├── inspection_agent.py
│   │   │   └── rules/                     ← YAML safety rules per agent
│   │   │       ├── asset_rules.yaml
│   │   │       ├── wo_rules.yaml
│   │   │       ├── pm_rules.yaml
│   │   │       ├── parts_rules.yaml
│   │   │       └── inspection_rules.yaml
│   │   │
│   │   ├── analysis/                      ← NEW — Layer 6: orchestration analysis
│   │   │   ├── orchestrator.py            ← Collects AgentResults, runs Layer 6 cycle + EL-6.x
│   │   │   └── action_schema.py           ← CMSDecision Pydantic model
│   │   │
│   │   ├── prompt_engine/
│   │   │   ├── engine.py
│   │   │   ├── ab_testing.py
│   │   │   └── templates/
│   │   │       ├── pdf/
│   │   │       │   ├── inspection_report.j2
│   │   │       │   ├── vendor_invoice.j2
│   │   │       │   ├── equipment_manual.j2
│   │   │       │   ├── compliance_cert.j2
│   │   │       │   └── field_notes.j2
│   │   │       ├── excel/
│   │   │       │   └── generic_excel.j2
│   │   │       ├── word/
│   │   │       │   └── generic_word.j2
│   │   │       └── csv/
│   │   │           └── schema_mapper.j2
│   │   │
│   │   ├── batch/
│   │   │   └── batch_processor.py         ← batch result processing runs EL-2.1/2.2/2.3 per result
│   │   │
│   │   ├── review_queue/
│   │   │   ├── queue.py
│   │   │   ├── corrections_log.py
│   │   │   └── websocket.py
│   │   │
│   │   └── models/
│   │       └── ingestion.py               ← ✅ BUILT — 9 new DB tables live in Azure
│   │
│   ├── tests/
│   └── pyproject.toml
│
├── svc-query/                             ← NEW — Sprint 2
│   ├── src/
│   │   ├── app.py
│   │   ├── config.py
│   │   ├── intent_classifier.py           ← 5 intent types (see section 14)
│   │   ├── tiers/
│   │   │   ├── structured_query.py        ← Tier 1: SQL gen → PostgreSQL + EL-7.QUERY
│   │   │   ├── fetch_then_read.py         ← Tier 2: metadata → Blob → Claude + EL-7.QUERY
│   │   │   └── vector_search.py           ← Tier 3: pgvector (manuals only) + EL-7.QUERY
│   │   ├── document_generator/            ← NEW — document generation pipeline
│   │   │   ├── planner.py                 ← Sonnet generates DocumentPlan JSON (N=3 vote)
│   │   │   ├── renderer.py                ← Executes plan → actual file (deterministic)
│   │   │   ├── validator.py               ← EL-7.DOC.PLAN: validates plan + verifies data sources
│   │   │   ├── filler.py                  ← EL-7.TEMPLATE: resolves + verifies all placeholders
│   │   │   └── base_templates/            ← Base DOCX/XLSX skeletons
│   │   │       ├── pm_schedule.docx
│   │   │       ├── wo_report.docx
│   │   │       ├── wo_package.docx
│   │   │       ├── parts_reorder.xlsx
│   │   │       ├── inspection_template.docx
│   │   │       ├── asset_health_summary.docx
│   │   │       └── maintenance_calendar.xlsx
│   │   ├── synthesiser.py
│   │   ├── output_renderer.py             ← text / json / word / pdf / xlsx
│   │   ├── eval_layer.py                  ← EL-7.DOC.RENDER + EL-7.DOC.EVAL (spot-checks + score)
│   │   └── query_audit.py
│   ├── tests/
│   └── pyproject.toml
│
├── shared-lib/                            ← ✅ BUILT — shared Python package
│   ├── cafm_shared/
│   │   ├── telemetry.py                   ← ✅ BUILT — configure_telemetry()
│   │   ├── metrics.py                     ← ✅ BUILT — 13 Prometheus metrics
│   │   ├── logging.py                     ← ✅ BUILT — structlog + OTel correlation
│   │   ├── exceptions.py                  ← ✅ BUILT — re-exports 16 CAFMError classes
│   │   └── models/__init__.py             ← ✅ BUILT — re-exports 50 plenum_cafm models
│   └── pyproject.toml
│
├── svc-ai-schema-mapper/                  ← Sprint 2 — LangGraph migration service (port 8003)
│   ├── Dockerfile
│   ├── src/
│   │   ├── app.py                         ← FastAPI app (migration + schema mapping endpoints)
│   │   ├── config.py
│   │   ├── worker.py                      ← ARQ worker (run_schema_mapping / resume_schema_mapping)
│   │   ├── graph/                         ← LangGraph 9-node state machine
│   │   │   ├── state.py
│   │   │   ├── nodes/
│   │   │   └── graph.py
│   │   └── ...
│   └── pyproject.toml
│
├── svc-ai-schema-mapper-ui/               ← Sprint 2 — React frontend (port 3001)
│   ├── Dockerfile                         ← nginx-served production build
│   ├── src/
│   │   ├── App.tsx                        ← 4-section nav: Migration | Schema | Doc RAG | Table Editor
│   │   ├── api.ts                         ← 3 base URLs: _apiBase (8003), _connectorBase (8000), _docRagBase (8004)
│   │   ├── hooks/
│   │   │   ├── useMigration.ts
│   │   │   └── useSchemaMapping.ts
│   │   └── components/
│   │       ├── UploadPanel.tsx
│   │       ├── PipelineTracker.tsx
│   │       ├── MigrationContent.tsx
│   │       ├── schema/
│   │       │   ├── SchemaStartPanel.tsx
│   │       │   ├── SchemaPipelineTracker.tsx
│   │       │   └── SchemaContent.tsx
│   │       ├── docrag/
│   │       │   └── DocRagPanel.tsx
│   │       └── tableCustomizer/
│   │           └── TableCustomizerPanel.tsx  ← Table Editor UI (inline edit, add/drop col, pagination)
│   └── package.json
│
└── doc-rag-main/                           ← Document RAG service (port 8004)
    ├── Dockerfile
    ├── app/
    │   └── main.py                         ← FastAPI, Claude PDF extraction, pgvector search
    ├── scripts/
    │   └── init_db.py
    └── pyproject.toml
```

---

## 4. Phase 1 — What is already built (DO NOT REBUILD)

The following are complete, tested, and running on Azure. Never rewrite these.

| Module | File | Status |
|--------|------|--------|
| Telemetry | `shared-lib/cafm_shared/telemetry.py` | ✅ Live |
| Metrics | `shared-lib/cafm_shared/metrics.py` | ✅ Live — 13 metrics |
| Logging | `shared-lib/cafm_shared/logging.py` | ✅ Live — trace_id in every log |
| Exceptions | `shared-lib/cafm_shared/exceptions.py` | ✅ Live |
| ORM re-exports | `shared-lib/cafm_shared/models/__init__.py` | ✅ Live — 50 models |
| Intermediate schema | `svc-ingestion/src/shared/intermediate_schema.py` | ✅ 12 models, 39 tests pass |
| DB session | `svc-ingestion/src/shared/db.py` | ✅ Live |
| Stage 1 Ingest | `svc-ingestion/src/shared/ingest.py` | ✅ Live |
| Stage 4 Unifier | `svc-ingestion/src/shared/unifier.py` | ✅ Live |
| Audit receipts | `svc-ingestion/src/shared/audit.py` | ✅ Live |
| ARQ worker stub | `svc-ingestion/src/worker.py` | ✅ Live |
| 9 new DB tables | `svc-ingestion/src/models/ingestion.py` | ✅ Live in Azure PostgreSQL |
| svc-ingestion app | `svc-ingestion/src/app.py` | ✅ Running port 8001 |
| svc-query app | `svc-query/src/app.py` | ✅ Running port 8002 |
| svc-ai-schema-mapper | `svc-ai-schema-mapper/src/app.py` | ✅ Running port 8003 |
| doc-rag | `doc-rag-main/app/main.py` | ✅ Running port 8004 |
| table-editor | `cafm-connector-service/src/cafm_connector/api/routes/plenum_cafm/table_customizer.py` | ✅ Running port 8005 |
| svc-ai-schema-mapper-ui | `svc-ai-schema-mapper-ui/` | ✅ Running port 3001 |
| Fiix SQL migrations | `cafm-connector-service/migrations/fiix_schema_expansion.sql` | ✅ 8 sections, 50 ORM classes |
| Table Customizer API | `/api/v1/plenum/tables/...` (8 endpoints) | ✅ Live on cafm-connector-service |
| Observability | Tempo + Prometheus + Grafana | ✅ All scraping |
| All 12+ services | docker-compose.yml | ✅ Healthy |

---

## 5. The standardised intermediate JSON

Contract between every ingestion agent (Layer 2) and the shared pipeline.
Defined in `svc-ingestion/src/shared/intermediate_schema.py` — already built.

```json
{
  "ingestion_id": "uuid",
  "source_type": "pdf|excel|word|csv|xml|json|database|api",
  "agent_id": "pdf-agent|excel-agent|word-agent|csv-agent|xml-json-agent",
  "source_filename": "inspection_report_nov_2025.pdf",
  "source_blob_url": "https://plenumstorage.blob.core.windows.net/...",
  "extracted_at": "2025-11-15T10:30:00Z",
  "extraction_method": "claude-vision|openpyxl+claude|pandoc+claude|pandas+claude|lxml+claude",
  "model_used": "claude-sonnet-4-6|claude-opus-4-6|claude-haiku-4-5|none",

  "entities": {
    "assets":       [...],
    "work_orders":  [...],
    "readings":     [...],
    "findings":     [...],
    "technicians":  [...],
    "vendors":      [...],
    "certificates": [...],
    "spare_parts":  []
  },

  "confidence": {
    "overall": "high|medium|low",
    "per_field": {"asset_id": "high", "serial": "medium"},
    "eval_score": 0.94,
    "rules_passed": true,
    "rules_violations": ["contradiction: Normal reading + Critical severity on AHU-004"]
  },

  "audit": {
    "prompt_template_id": "uuid|null",
    "prompt_version": "1.2|null",
    "passes": 1,
    "tokens_in": 4521,
    "tokens_out": 892,
    "cache_read_tokens": 3100,
    "cost_usd": 0.021,
    "cost_aed": 0.077,
    "processing_ms": 8400
  }
}
```

---

## 6. Layer 2 — Ingestion agent specifications

Every ingestion agent follows the same 4-step evaluation wrapper. No agent
writes to Layer 4 without passing all 3 evaluation checks (EL-2.1, EL-2.2, EL-2.3).

```
INGESTION AGENT STANDARD FLOW (all 5 agents follow this):

1. EL-2.0  PRE-VALIDATION (before agent runs)
   - File type matches expected MIME type
   - File size within limits (PDF: 32MB, Excel: 100MB, CSV: no limit)
   - File not corrupted (SHA-256 computable)
   - Duplicate check: SHA-256 against ingestion_documents
   PASS → proceed  FAIL → reject, log to ingestion_audit_log, notify user

2. EXTRACTION  (agent-specific — see below)
   - Agent reads source, calls Claude if needed, produces intermediate JSON

3. EL-2.1  RAW EXTRACTION OUTPUT EVAL
   - Response is valid JSON (not truncated, not garbled)
   - All required top-level keys present (entities, confidence, audit)
   - No null ingestion_id or source_type
   PASS → EL-2.2  FAIL → retry up to 3× with parse error context appended

4. EL-2.2  INTERMEDIATE JSON SCHEMA CONFORMANCE
   - Pydantic validates every entity in entities{}
   - per_field confidence tags present on every extracted field
   - eval_score field present (will be populated by EL-2.3)
   - No entity has a null primary identifier (asset_code, wo_code, etc.)
   PASS → EL-2.3  FAIL → route to review_queue with schema_violation flag

5. EL-2.3  LLM-AS-JUDGE EVAL (shared/eval_layer.py)
   - Haiku receives: source excerpt + extracted JSON
   - Returns eval_score (0.0–1.0) + contradiction list
   - eval_score written to confidence.eval_score in intermediate JSON
   - YAML rule engine runs: contradiction rules fire if present
   - eval_score ≥ 0.85 → auto-accept path
   - eval_score 0.60–0.84 → HITL review queue (pre-populated with extracted values)
   - eval_score < 0.60 → re-extract with correction context (max 3 attempts)
   PASS → confidence_router  FAIL (3 attempts) → status = manual_only

6. CONFIDENCE ROUTER  (shared/confidence_router.py)
   Reads eval_score + per_field confidence → routes to:
   - Layer 3 schema mapper (auto-accept)
   - review_queue (medium confidence)
   - re-extract queue (low confidence)
```

### Agent 1 — CSV Agent (`agents/csv_agent.py`)
```
Input:     .csv / .tsv files — NOTE: encoding=latin1 for known client files
Method:    pandas → Layer 3 schema mapper → asyncpg COPY
Process:   1. EL-2.0: file type + encoding check
           2. pd.read_csv(path, encoding='latin1')
           3. Pass headers + 50-row sample to Layer 3 schema mapper
           4. EL-3.0: check mapping confidence ≥ 0.80 before proceeding
           5. Rename columns per mapping
           6. EL-2.2: Pydantic validate mapped rows (sample check)
           7. Stream 1000-row batches → asyncpg COPY to target table
           8. EL-4.0: post-write row count check vs input row count
Special:   Claude called ONCE per file for schema mapping — not per row.
           Fastest agent. Direct asyncpg COPY, not ORM per row.
Known files: assets.csv · parts.csv · work_orders.csv · scheduled_pm.csv
             task_groups.csv · users.csv

EVAL NOTE: CSV agent does not call EL-2.3 (LLM-as-judge) — structured
           data has no ambiguity requiring LLM review. EL-3.0 mapping
           confidence check substitutes.
```

### Agent 2 — DOCX Agent (`agents/word_agent.py`)
```
Input:     .docx files — known: 7-section site inspection report (A–G)
Method:    python-docx table scan → Claude extracts JSON
Process:   1. EL-2.0: file type + page structure check
           2. Document(path) → scan all table rows for label:value pairs
           3. Claude Sonnet: extract {inspector_name, date, location, findings[]}
           4. EL-2.1: validate Claude returned parseable JSON
           5. EL-2.2: Pydantic validate intermediate JSON schema
           6. EL-2.3: LLM-as-judge eval_score on extracted findings
           7. Confidence router → accept / review queue / re-extract
           8. INSERT INTO inspections (asset_code, inspector, date, findings_jsonb)
Sections:  A: Inspector/Date/Location  B: Erosion/Sediment Controls
           C: Pollution Prevention     D: Stabilisation areas
           E: Discharge observations   F: Signature/certification
Special:   SOPs and manuals ALSO embed into pgvector (dual path — Tier 3)
```

### Agent 3 — PDF Agent (`agents/pdf_agent.py`)
```
Input:     .pdf files
Method:    Claude Vision — base64 inline (first time) / Files API (re-analysis)
Process:   1. EL-2.0: file type + size (≤32MB) + page count (≤100) check
           2. open(path,'rb') → base64.b64encode → b64_str
           3. messages=[{type:'document', source:{type:'base64',
              media_type:'application/pdf', data: b64_str}}]
           4. EL-2.1: validate Claude returned parseable JSON
           5. EL-2.2: Pydantic validate intermediate JSON schema
              - per_field confidence tags required on every field
              - document_type confidence must be high|medium (not low)
           6. EL-2.3: LLM-as-judge eval_score
              - YAML contradiction rules (e.g. Normal reading + Critical severity)
              - eval_score written to confidence.eval_score
           7. Confidence router → accept / review / re-extract
           8. Parse JSON response → same canonical schema as DOCX agent
Special:   - prompt caching (cache_control: ephemeral) — 90% cost saving
           - multi-pass voting (3x) for compliance certs and legal docs
             (EL-2.3 only passes if all 3 agree on key fields)
           - Batch API for bulk historical migration (50% cost reduction)
             (each batch result independently runs EL-2.1/2.2/2.3)
           - Files API: upload-once, reuse file_id for re-extractions
Models:    Sonnet (default), Opus (handwritten/legal), Haiku (classify-only)
Limits:    32MB max, 100 pages max
```

### Agent 4 — DB Agent (`agents/xml_json_agent.py` / existing connector)
```
Input:     Live CMMS SQL / NoSQL databases
Method:    SQLAlchemy engine introspects schema → delta sync
Process:   1. EL-2.0: connection test + schema introspection success check
           2. engine = create_engine(DB_URL) → inspect(engine).get_table_names()
           3. AI schema-map: customer columns → canonical fields
           4. EL-3.0: mapping confidence ≥ 0.80 check
           5. SELECT * FROM [table] WHERE updated_at > last_sync
           6. EL-2.2: Pydantic validate fetched rows (no null PKs, valid enums)
           7. pd.read_sql(query, engine) → upsert into unified store
           8. EL-4.0: upsert success count check
Special:   Uses existing cafm-connector-service plugins where possible.
           No Claude needed for already-structured data (no EL-2.3 needed).
```

### Agent 5 — Excel Agent (`agents/excel_agent.py`)
```
Input:     .xlsx / .xls / .xlsm
Method:    openpyxl → Layer 3 schema mapper → asyncpg COPY
Process:   1. EL-2.0: file type + workbook opens without error
           2. openpyxl sheet detection + formula resolution
           3. Layer 3 schema mapper (once per file)
           4. EL-3.0: mapping confidence ≥ 0.80 check
           5. EL-2.2: Pydantic validate mapped rows
           6. asyncpg COPY bulk insert
           7. EL-4.0: post-write row count check
Special:   Claude called ONCE per file for schema mapping (no EL-2.3 needed).
```

---

## 7. Layer 3 — AI schema mapper

**File:** `svc-ingestion/src/shared/schema_mapper.py`
**This is a new explicit layer — not baked into individual agents.**

Every ingestion agent calls the schema mapper after reading raw data.
The mapper is called once per new source and the result is cached in Redis.

### Canonical field registry

```
asset_code · asset_name · category · location_code · make · model · serial
wo_code · wo_priority · wo_status · wo_type · maintenance_type
sm_code · trigger_type · schedule_interval · sm_priority
part_code · stock_on_hand · minimum_allowed_stock · supplier · bom_group_name
user_full_name · user_title · user_name · reports_to
inspector_name · inspection_date · inspection_location · finding_type · risk_level
```

### How it works

```python
# Input: raw column headers from any source
headers = ["Asset Code", "Asset Name", "Work Order Priority", "SM Code", "Stock On Hand"]

# Claude Haiku prompt:
# "Map these CMMS columns to canonical fields. Return JSON {raw: canonical}."

# Output:
mapping = {
    "Asset Code": "asset_code",
    "Asset Name": "asset_name",
    "Work Order Priority": "wo_priority",
    "SM Code": "sm_code",
    "Stock On Hand": "stock_on_hand"
}

# Unmatched columns → raw_metadata JSONB (never dropped)
```

### Rules
- Result cached in Redis with key `schema_map:{source_hash}`
- Cache TTL: 24 hours
- Unmatched columns always preserved in `raw_metadata JSONB`
- **EL-3.0: If mapping confidence < 0.80, flag for human review before proceeding**
  - Agent does NOT write to Layer 4 until human approves the mapping
  - Mapping held in review_queue with mapping_review flag
  - Human confirms or corrects mapping → cached → agent resumes

---

## 7a. svc-AI-Schema-Mapper — Universal CMMS Migration Service

**What it is:** A LangGraph-compiled state machine (port 8003) that accepts any customer
CMMS export (CSV, Excel), auto-detects structure, maps fields through a deterministic
4-tier strategy, detects hierarchical relationships (sites → locations → assets),
validates data, and produces IntermediateSchema for handoff to svc-ingestion.

**Where it sits:** Between Layer 2 ingestion agents and Layer 4 database write. Runs once
per new customer CMMS source — result cached in Redis.

```
Customer CMMS Export (CSV / Excel / Database)
              │
              ▼
┌─────────────────────────────────────────────┐
│    svc-AI-Schema-Mapper (port 8003)         │
│    LangGraph + Postgres checkpointer        │
│    9-node pipeline with 3 HITL gates        │
└────────────────┬────────────────────────────┘
                 │
                 ▼ IntermediateSchema
                 │ (same contract as agents)
             svc-ingestion
```

### 9-Node LangGraph Pipeline

| Node | Name | Purpose | HITL Gate? |
|------|------|---------|-----------|
| 1 | `ingest_and_configure` | Auto-detect encoding, delimiter, data types; load CMMS mapping doc into state | — |
| 2 | `deterministic_map` (Tier 1) | 4-strategy deterministic mapping: exact match → aliases → regex → Haiku constrained | — |
| 3 | `semantic_map` (Tier 2) | Embedding-based cosine similarity for unresolved fields | — |
| 4 | `human_review` | ⏸ **GATE 1**: Approve/correct low-confidence field mappings (0.65–0.85) | **YES** |
| 5 | `preprocess_and_validate` | Dedup, null handling, type coercion, JSON Schema validation, FK checks | — |
| 6 | `resolve_hierarchy` | Detect FK relationships, implicit hierarchies, cycles, orphans; LLM classifies relationships | — |
| 7 | `verify_hierarchy` | ⏸ **GATE 2**: Customer approves/corrects detected sites → locations → assets → WOs | **YES** |
| 8 | `generate_output` | Produce nested JSON, CSV flat export, SQL statements, PDF summary; upload to Blob | — |
| 9 | `write_to_platform` | ⏸ **GATE 3**: Final confirmation before handing IntermediateSchema to svc-ingestion | **YES** |

All state transitions saved to Postgres checkpointer — customer can close browser and resume later.

### Tier 1 — Deterministic Mapping (4 strategies in sequence)

```
Strategy 1: Exact field name match
           "Asset Code" → "asset_code"  [confidence: 0.99]

Strategy 2: Alias lookup from CMMS mapping doc
           "EQUIP#" → ("equipment" alias) → "asset_code"  [confidence: 0.95–0.98]

Strategy 3: CMMS naming pattern regex
           "^ASSET_.*" → asset_code pattern  [confidence: 0.90–0.94]

Strategy 4: Haiku constrained call (if ≥0.85 required)
           "Some weird vendor field" + {context}
           → JSON {source: target, confidence}  [confidence: 0.85–0.92]

Gate: Fields < 0.85 confidence passed to Tier 2
```

### Tier 2 — Semantic Mapping (Embedding similarity)

```
Unresolved fields embedded (voyage-3 / text-embedding-3)
Cosine similarity vs pre-cached canonical schema embeddings

Score ≥ 0.85 → auto-accept
0.65–0.85 → GATE 1 (human review + suggestions)
< 0.65 → marked unmappable, raw_metadata preserved
```

### EL-3.0 Evaluation Layer

Applied after Node 2 deterministic mapping completes:

```
- Mapping overall confidence computed (avg of strategy confidence scores)
- If overall confidence < 0.80 → GATE 1 triggered (human review mandatory)
- If overall confidence ≥ 0.80 → skip GATE 1, proceed to Node 5
- LangSmith trace stored with every mapping decision
- Human correction feedback written back to LangSmith for prompt refinement
```

### LangSmith Integration (Primary observability for this service)

Set environment variables (automatic tracing — zero instrumentation code needed):
```python
LANGSMITH_API_KEY      # from smith.langchain.com
LANGSMITH_PROJECT      = "cafm-ai-schema-mapper"
LANGSMITH_ENDPOINT     = "https://api.smith.langchain.com"
LANGSMITH_TRACING      = True
```

Every migration run produces a LangSmith trace containing:
- Full graph execution trace (every node as span)
- Every LLM call (exact prompts, responses, token counts)
- LangGraph interrupt events (when paused, resumed, state)
- Conditional edge decisions (which branch taken, why)
- Error stacks with state snapshots

All traces tagged and searchable by `migration_id`:
```python
config = {
    "run_name": f"migration:{migration_id}",
    "tags": ["svc-ai-schema-mapper", f"cmms:{cmms_name}", f"org:{org_id}"],
    "metadata": {"migration_id": str(migration_id), "cmms_name": cmms_name, ...}
}
```

Human corrections (Node 4 HITL) written back as LangSmith feedback, enabling
weekly prompt refinement loop (same pattern as svc-ingestion Task 3.5).

### Output: IntermediateSchema

Node 8 generates the same IntermediateSchema Pydantic model used by all ingestion
agents. No separate schema contract — svc-AI-Schema-Mapper is a pre-ingestion
translation layer that produces the exact same output format as the PDF/DOCX/CSV agents.

```json
{
  "ingestion_id": "uuid",
  "source_type": "csv|excel|database",
  "agent_id": "schema-mapper",
  "source_filename": "assets_maximo_export.csv",
  "source_blob_url": "https://...",
  "extracted_at": "2026-04-01T...",
  "entities": {
    "assets": [...mapped rows...],
    "work_orders": [...],
    "parts": [...]
  },
  "confidence": {
    "overall": "high|medium|low",
    "mapping_coverage": 0.98,
    "hierarchy_confidence": 0.92
  },
  "audit": {
    "mapping_tier_distribution": {"t1": 35, "t2": 8, "t3": 0, "unresolved": 0},
    "human_review_gates_passed": 3,
    "processing_ms": 45000
  }
}
```

This output passes through the same EL-2.x, EL-3.0, EL-4.0 pipeline as structured data.
No exceptions.

---

## 7b. Table Customizer / Table Editor

**What it is:** A FastAPI sub-application that exposes 8 REST endpoints for
browsing, editing, and modifying `plenum_cafm` schema tables directly.
Served by `cafm-connector-service` at `/api/v1/plenum/tables/...` and also as
a standalone app on port 8005 for Azure container deployment.

**File:** `cafm-connector-service/src/cafm_connector/api/routes/plenum_cafm/table_customizer.py`

### How it works

```
cafm-connector-service (port 8000)
  └── /api/v1/plenum/tables/...   ← table_customizer_router included in plenum_router

table-editor standalone (port 8005)
  └── /table-editor/tables/...   ← TABLE_EDITOR_STANDALONE_MOUNT=1 wraps in a shell app
```

The file defines `table_editor_inner = FastAPI(...)` as a sub-application.
When mounted standalone (`TABLE_EDITOR_STANDALONE_MOUNT=1`), it creates a shell
gateway app that mounts the sub-app at `/table-editor`.

### Security model (CRITICAL — never bypass)

All table/column names go through **two layers** of validation before any SQL runs:

1. **Regex guard** — `_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")`
2. **Allow-list** — name must exist in `information_schema.tables` or `information_schema.columns`

DDL operations (`ADD COLUMN`, `DROP COLUMN`) additionally require `?confirm=true` query param.
The `id` column is protected from drop.

### 8 endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/tables` | List all tables in `plenum_cafm` schema with row estimates |
| GET | `/tables/{table}/columns` | List columns with type, nullable, default |
| GET | `/tables/{table}/rows` | Paginated rows (offset + limit) |
| POST | `/tables/{table}/rows` | Insert a new row (JSON body) |
| PATCH | `/tables/{table}/rows/{id}` | Update a row by UUID id |
| DELETE | `/tables/{table}/rows/{id}` | Delete a row by UUID id |
| POST | `/tables/{table}/columns?confirm=true` | Add a column (DDL) |
| DELETE | `/tables/{table}/columns/{col}?confirm=true` | Drop a column (DDL) |

### React UI (`svc-ai-schema-mapper-ui`)

**Component:** `src/components/tableCustomizer/TableCustomizerPanel.tsx`

- Left sidebar: table list with row estimates
- Main area: paginated data grid with inline cell editing (click → input → blur to save)
- Row add: inline new row form at top of table
- Row delete: hover → trash icon → confirmation modal
- Column add: modal with name / type / nullable
- Column drop: hover header → trash → confirmation modal

**API URL configuration in the frontend:**

The UI has three separate base URLs managed in `src/api.ts`:

| Variable | Default | Service |
|----------|---------|---------|
| `_apiBase` | `http://127.0.0.1:8003` | svc-ai-schema-mapper (migrations, schema mapping) |
| `_connectorBase` | `http://127.0.0.1:8000` | cafm-connector-service (table editor, CAFM CRUD) |
| `_docRagBase` | `http://127.0.0.1:8004` | doc-rag (document upload + RAG query) |
| `_tableEditorBase` | `http://127.0.0.1:8005` | table-editor standalone |

All four are configurable via the Settings panel (gear icon in top nav).

### Docker: standalone table-editor service

```yaml
# docker-compose.yml
table-editor:
  command: >
    bash -c "pip install -q -e /app/shared-lib &&
             uvicorn cafm_connector.api.routes.plenum_cafm.table_customizer:app
             --host 0.0.0.0 --port 8005 --reload"
  environment:
    TABLE_EDITOR_STANDALONE_MOUNT: "1"
  ports:
    - "8005:8005"
```

### Docker: shared-lib installation pattern

**Problem:** The `cafm-connector-service` Dockerfile build context is `./cafm-connector-service`,
so `shared-lib/` (at repo root) cannot be copied during the image build.

**Solution:** Volume-mount `/app/shared-lib` at runtime and install it in the container
startup command:

```yaml
command: >
  bash -c "pip install -q -e /app/shared-lib &&
           uvicorn cafm_connector.api.app:app --host 0.0.0.0 --port 8000 --reload"
volumes:
  - ./shared-lib:/app/shared-lib
```

This applies to: `api`, `worker`, and `table-editor` services in docker-compose.yml.
For production Azure Container Apps, the Dockerfile build context should be changed to
the repo root so shared-lib can be copied during the image build.

---

## 8. Layer 4 — Unified PostgreSQL store

### EL-4.0 — Pre-write validation (runs before every Layer 4 write)
```
- Pydantic model validates every row before INSERT/COPY
- FK references verified: asset_code exists in assets, etc.
- No null primary keys (UUID PKs auto-generated if absent)
- Enum fields match allowed values (status, priority, category)
- Date fields are valid timestamps, not in the future unless explicitly allowed
- Numeric fields within physical bounds (no negative stock, no 200°C AHU temp)
PASS → write proceeds
FAIL → row logged to ingestion_audit_log with validation_error, skipped (not dropped)
       Accumulates in failed_rows JSONB on the ingestion_documents record
```

### Known real data (current client)
| Table | Rows | Notes |
|-------|------|-------|
| assets | 60 | 11 categories, MOB-* codes |
| work_orders | 74 | 17 open, 4 Highest priority |
| parts | 38 | 19 below minimum stock — MOTOR-8HP at 0 (CRITICAL) |
| scheduled_pm | 7 | 6 time-based, 1 meter-based (generator 1000hr) |
| inspections | varies | From DOCX/PDF agents — findings JSONB |

### The `inspections` table (NEW — requires Alembic migration 002)
```sql
CREATE TABLE plenum_cafm.inspections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_code      VARCHAR(50) REFERENCES plenum_cafm.assets(asset_code),
    inspector       VARCHAR(255),
    inspection_date DATE,
    section         VARCHAR(10),          -- A through G
    finding_type    VARCHAR(100),
    observations    TEXT,
    risk_level      VARCHAR(20),          -- High | Medium | Low
    corrective_action BOOLEAN DEFAULT false,
    source_file     VARCHAR(500),         -- original blob URL
    findings_jsonb  JSONB,                -- full raw extraction
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

### The 50 ORM model classes (never duplicate — always import from cafm-connector-service)

**File:** `cafm-connector-service/src/cafm_connector/models/plenum_cafm.py`
All in `schema="plenum_cafm"`. SQLAlchemy 2.0 `DeclarativeBase` / `Mapped` / `mapped_column`.

| Group | Tables (29 core) |
|-------|--------|
| Auth/Org | organizations, users, roles, permissions, user_roles, role_permissions |
| Facilities | locations, asset_categories, assets, asset_documents, asset_readings |
| Maintenance | maintenance_plans, technicians, technician_skills |
| Vendors | vendors, vendor_contacts, vendor_contracts, sla_policies |
| Work Orders | work_orders, work_order_tasks, work_order_comments, work_order_attachments, work_order_history |
| History | maintenance_history |
| Inventory | spare_parts, inventory_transactions, work_order_parts |
| System | notifications, audit_logs |

**Fiix expansion models (+14) — added in `fiix_schema_expansion.sql` Section 6:**

| Table | Purpose |
|-------|---------|
| `misc_cost_types` | Lookup for miscellaneous cost categories |
| `files` | Polymorphic file attachments (linked to any entity via entity_type + entity_id) |
| `bom_group_parts` | Bill-of-materials parts linked to BOM groups |
| `schedule_triggers` | Multiple triggers per scheduled maintenance plan |
| `misc_costs` | Miscellaneous costs on work orders or assets |
| `asset_offline_log` | Asset downtime log with computed `downtime_minutes` (generated column) |
| `receipts` | Purchase receipts — status CHECK constraint: pending/partial/complete/cancelled |
| `receipt_line_items` | Line items on receipts (FK to receipts + spare_parts) |
| `rca_problems` | Root-cause analysis: problem statements |
| `rca_causes` | Root-cause analysis: causes linked to problems |
| `rca_actions` | Root-cause analysis: corrective actions |
| `rca_groupings` | RCA groupings (for aggregating across incidents) |
| `rca_grouping_causes` | Many-to-many: groupings ↔ causes |
| `rca_grouping_actions` | Many-to-many: groupings ↔ actions |

**Additional models (+7) — were missing from original set:**
`bom_groups`, `scheduled_maintenance`, `purchase_orders`, `purchase_order_lines`,
`asset_meters`, `meter_readings`, `work_order_labor`

**SQL migrations file:** `cafm-connector-service/migrations/fiix_schema_expansion.sql`
— 8 sections covering all Fiix CMMS schema expansions in order:
1. BOM groups + BOM group parts
2. Scheduled maintenance + multiple schedule triggers
3. Miscellaneous cost types + costs
4. Purchase orders + line items
5. Asset meters + meter readings + work order labor
6. 14 focused Fiix expansion tables (Section 6 — the main expansion)
7. Work order labor extensions
8. Index + FK alterations

### The 9 Sprint 2 ingestion tables (already live in Azure)
`ingestion_documents` · `ingestion_audit_log` · `prompt_templates` ·
`prompt_ab_tests` · `review_queue` · `corrections_log` ·
`claude_api_usage` · `claude_budget_config` · `query_audit_log`

---

## 9. Layer 5 — Specialist data agents + per-agent determinism

**This is the most important architectural decision in Sprint 2.**

Every specialist data agent runs its own full Bound → Aggregate → Constrain
determinism cycle before its result is passed to Layer 6. Layer 6 receives
5 pre-validated `AgentResult` objects — never raw data or strings.

**Evaluation layers are embedded at every step of the determinism cycle.**
The cycle is not complete unless all 4 evaluation checkpoints pass.

### The AgentResult contract (`data_agents/base_data_agent.py`)

```python
class AgentResult(BaseModel):
    agent_id: str
    domain: Literal["asset", "wo", "pm", "parts", "inspection"]
    status: str                    # agent-specific status enum value
    confidence: float              # 0.0 – 1.0
    reasoning: str                 # max 60 words
    runs: list[SingleRunResult]    # all 3 individual run outputs
    runs_agreed: int               # 1, 2, or 3
    hard_rules_fired: list[str]    # which YAML rules overrode AI
    requires_human_review: bool
    raw_data: dict                 # validated SQL rows that fed this agent
    audit_id: UUID                 # written to agent_audit_log before return
```

### The reusable determinism wrapper (`shared/agent_determinism.py`)

Written once. Every data agent calls it. Each step has an embedded eval layer.

```python
class AgentDeterminismCycle:
    """
    Runs Bound → Aggregate → Constrain for any specialist data agent.
    Each agent provides: pydantic_schema, rules_yaml_path, threshold,
    model, n_runs, system_prompt_template.
    """

    async def run(
        self,
        raw_rows: list[dict],
        agent_id: str,
        context: dict,
    ) -> AgentResult:

        # EL-5.BOUND — validate every row before AI sees it
        validated = self._bound(raw_rows)

        # EL-5.AGG — N runs, each run output validated before vote
        runs = await self._aggregate(validated, context)

        # EL-5.VOTE — majority vote integrity check
        winner = self._majority_vote(runs)

        # EL-5.CONSTRAIN — hard rules + confidence gate + audit
        result = await self._constrain(winner, runs, validated)

        return result

    def _bound(self, rows: list[dict]) -> list[dict]:
        """
        EL-5.BOUND: Pydantic validates every row.
        Rejects nulls, wrong types, impossible values.
        Raises BoundValidationError if any row fails.
        Rejected rows logged to agent_audit_log — never silently dropped.
        PASS → validated rows proceed to aggregate
        FAIL → BoundValidationError raised, agent halts, requires_human_review=True
        """
        ...

    async def _aggregate(self, validated: list[dict], context: dict):
        """
        Fires N=3 Claude calls concurrently (asyncio.gather).
        EL-5.AGG: Each run output validated before being added to runs list:
          - Response must parse as valid JSON
          - status field must be in the agent's allowed enum values
          - confidence must be a float in 0.0–1.0
          - reasoning must be present and ≤ 60 words
        Invalid run output → that run marked as failed, not counted in vote.
        If < 2 valid runs → requires_human_review = True.
        """
        ...

    def _majority_vote(self, runs: list[SingleRunResult]) -> SingleRunResult:
        """
        EL-5.VOTE: Vote on the key status field across all valid runs.
        Tiebreak: highest confidence wins.
        Integrity check: if all 3 runs disagree (3-way split), requires_human_review = True.
        Logs runs_agreed count to AgentResult.
        """
        ...

    async def _constrain(self, winner, runs, validated) -> AgentResult:
        """
        EL-5.CONSTRAIN:
        1. Apply YAML hard rules (these always override AI vote — no exceptions)
        2. Apply confidence gate (agent-specific threshold — see each agent spec)
           confidence < threshold → requires_human_review = True
        3. Write to agent_audit_log (all 3 runs, winner, hard rules fired)
        4. Set requires_human_review flag
        PASS → AgentResult returned to Layer 6
        FAIL (confidence gate) → AgentResult returned with requires_human_review=True
        """
        ...
```

---

### Asset Agent (`data_agents/asset_agent.py`)

```
SQL:       SELECT * FROM assets
           LEFT JOIN work_orders USING(asset_code)
           WHERE asset_code = $1

EL-5.BOUND:
           - asset_code not null
           - category in known enum (Air Handler, Boiler, Chiller, etc.)
           - location_code resolves in locations table
           FAIL → BoundValidationError, agent halts

AGGREGATE: Model: claude-haiku-4-5  |  N=3  |  concurrent asyncio.gather
           Vote on: status: operational | at_risk | critical
           Prompt: "Given this asset's open WOs and last PM date,
                   what is its operational status?"

EL-5.AGG (per run):
           - status must be one of: operational | at_risk | critical
           - confidence in 0.0–1.0
           - reasoning ≤ 60 words
           Invalid run → marked failed, excluded from vote

EL-5.VOTE: Threshold: 0.80
           3-way split → requires_human_review = True

EL-5.CONSTRAIN — Hard rules (asset_rules.yaml):
           - open_wo_count >= 3 AND any priority == 'Highest'
             → status = critical (overrides AI — always)
           - last_pm_date > 90 days ago AND has_scheduled_pm
             → status = at_risk (overrides AI if AI said operational)
           - asset_code not found → hard stop, do not pass to Layer 6
           Confidence < 0.80 → requires_human_review = True

OUTPUT:    AgentResult.status: operational | at_risk | critical
```

---

### WO Agent (`data_agents/wo_agent.py`)

```
SQL:       SELECT * FROM work_orders
           WHERE status = 'Open'
           ORDER BY priority DESC

EL-5.BOUND:
           - priority in [Highest, High, Medium, Low, Lowest]
           - status in [Open, Closed]
           - wo_code matches expected pattern
           - asset_code resolves in assets table
           FAIL → BoundValidationError, agent halts

AGGREGATE: Model: claude-haiku-4-5  |  N=3  |  concurrent asyncio.gather
           Vote on: triage: escalate | monitor | routine
           Prompt: "Should this WO be escalated given its age,
                   priority, and asset category?"

EL-5.AGG (per run):
           - triage must be one of: escalate | monitor | routine
           - confidence in 0.0–1.0
           Invalid run → marked failed, excluded from vote

EL-5.VOTE: Threshold: 0.82
           3-way split → requires_human_review = True

EL-5.CONSTRAIN — Hard rules (wo_rules.yaml):
           - priority == 'Highest' AND age_days > 7
             → triage = escalate (always, no AI override)
           - priority == 'Highest' AND age_days > 3
             → triage = escalate (always)
           - 4 or more Highest priority open WOs on same asset
             → triage = escalate + requires_human_review = true
           Known state: 17 open WOs, 4 at Highest priority
           Confidence < 0.82 → requires_human_review = True

OUTPUT:    AgentResult.status: escalate | monitor | routine
```

---

### PM Agent (`data_agents/pm_agent.py`)

```
SQL:       SELECT sm.*, a.asset_code, a.category
           FROM scheduled_pm sm
           JOIN assets a USING(asset_code)

EL-5.BOUND:
           - trigger_type in ['t', 'm'] (time-based or meter-based)
           - schedule_interval is positive integer
           - last_date is valid date and NOT in the future
           - for meter trigger: meter_reading must be present
           FAIL → BoundValidationError, agent halts

AGGREGATE: Model: claude-haiku-4-5  |  N=3  |  concurrent asyncio.gather
           Vote on: pm_status: overdue | due_soon | ok
           Prompt: "Is this PM overdue, due soon, or OK given
                   the trigger type and last completion?"
           Note: Lower threshold — date math is largely deterministic

EL-5.AGG (per run):
           - pm_status must be one of: overdue | due_soon | ok
           - confidence in 0.0–1.0
           Invalid run → marked failed, excluded from vote

EL-5.VOTE: Threshold: 0.75
           3-way split → requires_human_review = True

EL-5.CONSTRAIN — Hard rules (pm_rules.yaml):
           - trigger_type == 't': due_date = last_date + interval_months
             → if due_date < today: pm_status = overdue (date math wins)
           - trigger_type == 'm': if meter_reading unavailable
             → pm_status = due_soon (fail safe — never 'ok')
           - Generator 1000hr (meter) at 0 stock MOTOR-8HP
             → pm_status = overdue + requires_human_review = true
           Known: 6 time-based PMs + 1 meter PM (generator 1000hr)
           Confidence < 0.75 → requires_human_review = True

OUTPUT:    AgentResult.status: overdue | due_soon | ok
```

---

### Parts Agent (`data_agents/parts_agent.py`)

```
SQL:       SELECT * FROM parts
           WHERE stock_on_hand < minimum_allowed_stock

EL-5.BOUND:
           - stock_on_hand >= 0 (negative stock rejected)
           - minimum_allowed_stock > 0
           - part_code not null
           - supplier field present
           FAIL → BoundValidationError, agent halts

AGGREGATE: Model: claude-haiku-4-5  |  N=3  |  concurrent asyncio.gather
           Vote on: urgency: critical | severe | low | ok
           Prompt: "What is the reorder urgency for this part
                   given stock levels and linked asset criticality?"

EL-5.AGG (per run):
           - urgency must be one of: critical | severe | low | ok
           - confidence in 0.0–1.0
           Invalid run → marked failed, excluded from vote

EL-5.VOTE: Threshold: 0.78
           3-way split → requires_human_review = True

EL-5.CONSTRAIN — Hard rules (parts_rules.yaml):
           - stock_on_hand == 0
             → urgency = critical (always — AI cannot override this)
           - stock_on_hand < (minimum_allowed_stock * 0.25)
             → urgency = severe (always)
           - part linked to asset with open Highest priority WO
             → bump urgency one level (low→severe, severe→critical)
           BOM match: cross-ref bom_group_name → linked assets
           Known: 19 parts below min, MOTOR-8HP at 0 (CRITICAL)
           Confidence < 0.78 → requires_human_review = True

OUTPUT:    AgentResult.status: critical | severe | low | ok
           Per-part breakdown in raw_data
```

---

### Inspection Agent (`data_agents/inspection_agent.py`)

```
SQL:       SELECT * FROM inspections
           WHERE corrective_action = true
           ORDER BY inspection_date DESC

EL-5.BOUND:
           - section in ['A','B','C','D','E','F','G']
           - finding_type in known enum
           - inspection_date not in the future
           - asset_code resolves in assets table
           FAIL → BoundValidationError, agent halts

AGGREGATE: Model: claude-sonnet-4-6  |  N=3  |  concurrent asyncio.gather
           (Sonnet — natural language findings require more nuanced reasoning)
           Vote on:
             risk_level: High | Medium | Low
             requires_corrective_action: bool
           Prompt: "What is the risk level of this inspection finding
                   and does it require corrective action?"

EL-5.AGG (per run):
           - risk_level must be one of: High | Medium | Low
           - requires_corrective_action must be bool
           - confidence in 0.0–1.0
           Invalid run → marked failed, excluded from vote

EL-5.VOTE: Threshold: 0.85  (highest — compliance impact)
           3-way split → requires_human_review = True

EL-5.CONSTRAIN — Hard rules (inspection_rules.yaml):
           - observations contains ['corrective', 'immediate', 'urgent',
             'failed', 'critical'] → requires_corrective_action = true
             (always — language check overrides AI)
           - risk_level == 'High'
             → requires_human_review = true (always, regardless of confidence)
           - Section B or C findings with corrective_action
             → escalate to WO Agent for work order creation
           Confidence < 0.85 → requires_human_review = True

OUTPUT:    AgentResult.status: risk_level value (High|Medium|Low)
           AgentResult.requires_human_review set if High risk
```

---

## 10. Layer 6 — Orchestration analysis

Receives 5 pre-validated `AgentResult` objects. Runs its own
Bound → Aggregate → Constrain cycle on top of already-clean data.
**Every step of this cycle has an evaluation layer.**

### The CMSDecision schema (`analysis/action_schema.py`)

```python
class CMSDecision(BaseModel):
    action: Literal["create_wo", "order_part", "alert_critical", "no_action"]
    asset_code: str
    priority: Literal["low", "medium", "high", "critical"]
    confidence: float              # 0.0 – 1.0
    reasoning: str                 # max 60 words
    contributing_agents: list[str] # which agents drove this decision
    runs_agreed: int               # out of 3
```

### Layer 6 determinism cycle with evaluation layers

```
EL-6.BOUND:
  - All 5 AgentResult objects present and typed (Pydantic validation)
  - No AgentResult has requires_human_review=true
    (if any does → action = human_review, skip AI entirely — no exceptions)
  - confidence fields all in 0.0–1.0 range
  - All audit_ids resolvable in agent_audit_log (proof of full determinism cycle)
  FAIL → CMSDecision(action="human_review") returned immediately, AI not called

AGGREGATE:
  - Model: claude-sonnet-4-6  |  N=3  |  concurrent asyncio.gather
  - Payload: all 5 AgentResult objects serialised as context
  - Vote on: action field (create_wo | order_part | alert_critical | no_action)
  - Example: Run1=create_wo(0.91), Run2=create_wo(0.94), Run3=create_wo(0.90)
    → Winner: create_wo, avg_confidence=0.917, runs_agreed=3

EL-6.AGG (per run):
  - action must be one of: create_wo | order_part | alert_critical | no_action
  - confidence must be float in 0.0–1.0
  - reasoning must be ≤ 60 words
  - contributing_agents must be non-empty list
  Invalid run → marked failed, not counted in vote
  If < 2 valid runs → action = human_review

EL-6.VOTE:
  - Majority vote on action field
  - 3-way split → action = human_review (cannot determine winner)
  - logs runs_agreed to CMSDecision

EL-6.CONSTRAIN:
  - Gate 1: confidence < 0.85 → downgrade to human_review
  - Gate 2: action == alert_critical → always human_review regardless of confidence
  - Gate 3: any hard rule fired in ANY agent → included in reasoning
  - Audit: INSERT orchestration_audit_log(uuid, timestamp, asset_code, action,
            confidence, reasoning, runs_agreed, safety_passed, agent_results_jsonb)
  - Audit log is INSERT ONLY — no UPDATE or DELETE ever
  PASS → CMSDecision delivered to Layer 7
  FAIL (gate 1/2) → CMSDecision(action="human_review") + audit entry
```

---

## 11. Layer 7 — Deterministic outputs

### A — Query answers (Tier 1/2/3)

| Output | Trigger | Implementation |
|--------|---------|----------------|
| ICC chat answer | `tier1_structured` | SQL-grounded answer — no hallucination |
| Document re-read | `tier2_document` | Blob fetch → Claude re-reads → answer |
| Manual answer | `tier3_manual` | pgvector chunks → synthesised answer |

**EL-7.QUERY — applies to all 3 tiers:**
```
- Every value in the answer must trace to a real DB row or document chunk
- Tier 1: answer generated from SQL result set only — no fabrication from training data
- Tier 2: answer generated from fetched document content only
- Tier 3: answer generated from retrieved vector chunks only
- If no grounding data found → answer = "No data found for this query"
  (never hallucinate an answer when the data does not exist)
- Token count check: if answer is suspiciously long vs source data, flag for review
```

### B — Orchestration outputs (Layer 6 actions)

| Output | Trigger | Implementation |
|--------|---------|----------------|
| Work order raised | `action == create_wo` AND `confidence >= 0.85` | Write to work_orders table + XLSX "AI RAISED" badge |
| Part order flagged | Parts agent `urgency == critical\|severe` | Parts XLSX AI Alert: ORDER NOW / REORDER SOON |
| DOCX Section F filled | Any inspection ingestion | `filler.py` populates Section A–G with real data |
| Audit log entry | Every action, every time | INSERT only — immutable — full agent chain |

**EL-7.ACTION — before any write from Layer 6 action:**
```
- Work order auto-raise: confidence must be ≥ 0.85 verified from CMSDecision
  (not assumed — explicitly checked before INSERT)
- Part order: urgency level cross-checked against parts table live value
  (not from cached AgentResult — re-query to confirm before flagging)
- Both actions: INSERT into work_orders / parts alert blocked if FK validation fails
- audit_id from orchestration_audit_log appended to every auto-raised record
```

### C — Document generation (document_generate / template_fill intents)

**EL-7.DOC.PLAN — DocumentPlan validation (validator.py):**
```
Runs immediately after the N=3 planning vote produces the winning DocumentPlan.
- DocumentPlan validates against Pydantic schema
- Every data_source in plan verified to resolve to a real table in plenum_cafm
- Every filter in data_source verified to return ≥ 1 row (dry-run query)
- Every asset_code / part_code in scope verified to exist in assets / parts table
- Output format is in supported list (docx | xlsx | pdf)
PASS → renderer.py proceeds
FAIL → re-plan once with validation error context
       If still fails after 2nd plan → error returned to user (no file generated)
```

**EL-7.DOC.RENDER — post-render spot check (eval_layer.py):**
```
Runs after renderer.py produces the file, before delivery.
- Haiku receives: 10 randomly selected values from the rendered document
- For each value: verify it exists in the source DB rows fetched in step 2
- Check: no asset_code in document is missing from assets table
- Check: no date in document is outside the range of source data
- Check: no numeric value in document deviates from source by more than rounding error
- Produces eval_score (0.0–1.0): proportion of spot-checked values that verified
- eval_score written to document_generation_log.eval_score
PASS (eval_score ≥ 0.85) → file delivered, audit receipt generated
FAIL (eval_score < 0.85) → held_for_review = True, file NOT auto-delivered
                           Human reviewer sees: file + failed spot-checks + source rows
```

**EL-7.TEMPLATE — template fill validation (filler.py):**
```
Runs in two passes: before fill and after fill.

Pre-fill (EL-7.TEMPLATE.PRE):
- Parse all {{table.field}} placeholders from template
- Verify every placeholder resolves to a real table.field combination
- Execute SQL to fetch values for every placeholder
- BLOCK render if any placeholder is unresolvable (no partial fills)

Post-fill (EL-7.TEMPLATE.POST):
- Scan rendered document: verify no {{...}} strings remain (all filled)
- For each filled value: verify it matches the DB row that sourced it
- eval_score computed same way as EL-7.DOC.RENDER
PASS → delivered  FAIL → held for review
```

### DOCX Section F decision block (auto-filled by Layer 6)
```
Decision:    create_wo — MOB-AHU-001 quarterly PM overdue
Confidence:  0.917
Runs agreed: 3/3
Agents:      PM Agent (overdue) + Asset Agent (at_risk)
Timestamp:   2026-03-25T14:22:00Z
Audit ID:    uuid
```

---

## 12. Two-level determinism — summary

This is what makes the platform enterprise-grade. Most systems have one
determinism check before output. This platform has two independent levels,
each with their own evaluation layers:

```
LEVEL 1 — Per-agent (Layer 5)
  EL-5.BOUND:     Every SQL row validated before AI sees it
  EL-5.AGG:       Every individual run output validated before vote
  EL-5.VOTE:      Majority vote integrity checked
  EL-5.CONSTRAIN: Hard rules fire + confidence gate + audit write
  Output: typed AgentResult with confidence score + audit_id
                    ↓
LEVEL 2 — Orchestration (Layer 6)
  EL-6.BOUND:     All 5 AgentResults present, typed, no human_review flags
  EL-6.AGG:       Each N=3 run output validated before vote
  EL-6.VOTE:      Majority vote integrity checked
  EL-6.CONSTRAIN: Confidence gate (≥ 0.85) + safety rules + immutable audit write
  Output: CMSDecision with action + confidence + reasoning + audit_id
```

**Why this matters:** If the PM agent miscalculates a due date but the hard
rule (trigger_type='t' → date math always wins) corrects it, Layer 6 never
sees the miscalculation. The error is caught and corrected at the source,
with a full audit trail showing what the AI said vs what the rule enforced.

---

## 13. New database tables (Sprint 2 additions — beyond Phase 1)

### Already live in Azure (Phase 1 — migration 001)
`ingestion_documents` · `ingestion_audit_log` · `prompt_templates` ·
`prompt_ab_tests` · `review_queue` · `corrections_log` ·
`claude_api_usage` · `claude_budget_config` · `query_audit_log`

### Requires migration 002 (Phase 2)

```
inspections              — from DOCX/PDF agents (see section 8)

agent_audit_log          — Layer 5 per-agent determinism audit
  id UUID PK
  agent_id               VARCHAR(50)         ← asset|wo|pm|parts|inspection
  domain                 VARCHAR(50)
  asset_code             VARCHAR(50) NULL
  bound_validation_passed BOOL               ← EL-5.BOUND result
  run_1_output           JSONB
  run_2_output           JSONB
  run_3_output           JSONB
  run_1_valid            BOOL               ← EL-5.AGG result per run
  run_2_valid            BOOL
  run_3_valid            BOOL
  runs_agreed            INT
  winner_status          VARCHAR(50)
  winner_confidence      NUMERIC(4,3)
  hard_rules_fired       JSONB               ← which YAML rules overrode AI
  final_status           VARCHAR(50)
  confidence_gate_passed BOOL               ← EL-5.CONSTRAIN result
  requires_human_review  BOOL
  model_used             VARCHAR(50)
  tokens_total           INT
  cost_usd               NUMERIC(10,6)
  timestamp              TIMESTAMPTZ

orchestration_audit_log  — Layer 6 full decision audit (immutable)
  id UUID PK
  asset_code             VARCHAR(50)
  bound_passed           BOOL               ← EL-6.BOUND result
  action                 VARCHAR(50)
  priority               VARCHAR(20)
  confidence             NUMERIC(4,3)
  reasoning              TEXT
  runs_agreed            INT
  run_1_valid            BOOL               ← EL-6.AGG per run
  run_2_valid            BOOL
  run_3_valid            BOOL
  confidence_gate_passed BOOL               ← EL-6.CONSTRAIN
  safety_passed          BOOL
  agent_results_jsonb    JSONB               ← all 5 AgentResults serialised
  hard_rules_fired       JSONB
  model_used             VARCHAR(50)
  tokens_total           INT
  cost_usd               NUMERIC(10,6)
  timestamp              TIMESTAMPTZ
  -- INSERT ONLY. No UPDATE or DELETE ever.

document_generation_log  — every generated/filled document
  id UUID PK
  request_text           TEXT
  intent_type            VARCHAR(30)
  document_type          VARCHAR(50)
  document_plan_json     JSONB               ← full DocumentPlan executed
  plan_validation_passed BOOL               ← EL-7.DOC.PLAN result
  output_format          VARCHAR(10)
  output_blob_url        TEXT
  data_sources           JSONB               ← tables + row counts consulted
  spot_checks_run        INT                ← EL-7.DOC.RENDER: values checked
  spot_checks_passed     INT                ← EL-7.DOC.RENDER: values verified
  eval_score             NUMERIC(4,3)       ← EL-7.DOC.EVAL score
  plan_runs_agreed       INT
  held_for_review        BOOL               ← true if eval_score < 0.85
  model_used             VARCHAR(50)
  tokens_in              INT
  tokens_out             INT
  cost_usd               NUMERIC(10,6)
  render_ms              INT
  user_id                UUID FK → users
  timestamp              TIMESTAMPTZ
```

---

## 14. Query layer + document generation (svc-query)

### Intent classification — 5 types

The intent classifier (Haiku) reads the user's request and routes to one
of five handlers. This replaces the previous 3-tier model.

```
User request
      ↓
Intent classifier (Haiku — < 500ms)
      ↓
┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
tier1           tier2          tier3          document_gen   template_fill
Structured      Fetch-then-    Vector         Generate a     Fill an
SQL query       read           search         new document   existing
                                              from scratch   template
~60%            ~20%           ~5%            ~10%           ~5%
└──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘
```

**Classifier prompt examples (few-shot, hard-coded):**
```
"Which assets have open WOs?"           → tier1_structured
"What did the Nov inspection say?"      → tier2_document
"What torque spec for AHU belt?"        → tier3_manual
"Build me a PM schedule for AHUs"       → document_generate
"Create a weekly WO report"             → document_generate
"Give me a parts reorder summary"       → document_generate
"Fill in the inspection template for
 AHU-004 with this week's data"         → template_fill
```

If classifier confidence < 0.80 → ask user clarifying question before proceeding.

---

### Tier 1 — Structured query (~60%)
- Haiku classifies → Sonnet generates parameterised SQL template + parameters
- Parameters injected safely — raw LLM SQL never executes directly
- Execute via asyncpg on read replica → Sonnet synthesises grounded answer
- **EL-7.QUERY**: answer verified to contain only values from SQL result set
- Example: "Which assets have open WOs?" → SQL → "17 open WOs: 4 Highest priority..."

### Tier 2 — Fetch-then-read (~20%)
- Metadata query finds exact doc in `ingestion_documents`
- Fetch from Azure Blob → send to Sonnet with user's question
- No vector search — metadata query finds the exact document
- **EL-7.QUERY**: answer verified to contain only values from fetched document

### Tier 3 — Vector search (~5% — manuals/SOPs only)
- pgvector on existing PostgreSQL — no separate vector DB
- DOCX agent embeds manual chunks at ingestion time (dual path)
- Semantic search → top-k chunks → Sonnet synthesises
- **EL-7.QUERY**: answer verified to cite only retrieved chunks

---

### document_generate intent (~10%)

This is the most complex path. Claude plans the document structure,
a deterministic renderer builds the file, the eval layer verifies
every value traces to a real DB row.

**The golden rule: Claude populates values. Claude never invents values.**
Every cell, figure, date, and name in a generated document must come
from a real database row. EL-7.DOC.EVAL enforces this after generation.

#### Step-by-step flow with evaluation layers

```
1. CLASSIFY
   Haiku identifies: document_generate
   Haiku also classifies document_type and required_data

2. DATA FETCH
   Structured SQL pulls all relevant rows from unified store
   EL-4.0: Rows validated against Pydantic schema before Claude sees them
   (same pre-write validation applied to data reads for consistency)

3. PLAN  (Sonnet — N=3, majority vote on structure)
   Prompt: "Given this data, design a document structure.
            Return DocumentPlan JSON only."
   Output: DocumentPlan JSON (see schema below)
   EL-6.AGG equivalent: each run output validated as valid DocumentPlan JSON
   Vote on: sections list — majority wins
   Never: free-form text generation at this stage

4. EL-7.DOC.PLAN — VALIDATE (validator.py)
   DocumentPlan validated against Pydantic schema
   Every data_source verified to resolve to real table
   Every filter verified to return ≥ 1 row (dry-run query)
   If validation fails → re-plan once → if still fails → error to user

5. RENDER  (deterministic — no Claude)
   renderer.py executes DocumentPlan section by section
   python-docx builds DOCX, openpyxl builds XLSX, reportlab builds PDF
   Values populated from DB rows fetched in step 2
   Claude does NOT touch the file

6. EL-7.DOC.RENDER + EL-7.DOC.EVAL (eval_layer.py)
   Haiku spot-checks 10 random values in the rendered document
   Verifies each value exists in the source DB rows (step 2)
   Checks no asset_code in document is missing from assets table
   eval_score written to document_generation_log
   eval_score < 0.85 → held_for_review = True, file NOT auto-delivered

7. DELIVER (only if eval_score ≥ 0.85)
   Return file as download + store in Azure Blob
   Write to document_generation_log with all eval fields populated
   Audit receipt includes: plan, data sources, eval score, render time,
                           spot_checks_run, spot_checks_passed
```

#### DocumentPlan schema

```python
class DocumentSection(BaseModel):
    type: Literal[
        "summary_table",
        "schedule_grid",
        "task_checklist",
        "parts_table",
        "findings_list",
        "kpi_summary",
        "signature_block",
        "free_text_header",   # Claude-generated text OK here only
    ]
    heading: str
    data_source: str          # SQL WHERE clause or table name
    columns: list[str] | None
    highlight_rule: str | None
    sort_by: str | None
    limit: int | None

class DocumentPlan(BaseModel):
    document_type: str
    title: str
    generated_for: str
    output_format: Literal["docx", "xlsx", "pdf"]
    sections: list[DocumentSection]
    footer: dict              # always includes: generated_by, timestamp, audit_id
    data_sources_required: list[str]
```

#### Supported document types

| Type | Output | Data sources | Description |
|------|--------|-------------|-------------|
| `pm_schedule` | DOCX/XLSX | assets, scheduled_pm, task_groups | PM schedule for asset category |
| `wo_report` | DOCX/XLSX | work_orders, assets | WO status report (open/closed/escalated) |
| `wo_package` | DOCX | work_orders, task_groups, parts | Single WO + task checklist + parts list |
| `parts_reorder` | XLSX | parts, assets | Reorder summary with CRITICAL/SEVERE/LOW |
| `inspection_template` | DOCX | assets, inspections | Pre-populated inspection form |
| `asset_health_summary` | DOCX | assets, work_orders, scheduled_pm | Asset health across all/filtered assets |
| `maintenance_calendar` | XLSX | scheduled_pm, assets | Monthly/quarterly PM calendar grid |
| `inspection_report` | DOCX | inspections, assets | Findings report with risk levels |
| `custom` | DOCX/XLSX/PDF | user-specified | Any combination of above sections |

#### Document generation determinism cycle

```
BOUND (EL-7.DOC.PLAN):
  - DocumentPlan validates against Pydantic schema
  - All data_source references resolve to real tables
  - All asset_codes / part_codes in scope exist in DB
  - Output format is in supported list

AGGREGATE (planning step only — N=3 Sonnet):
  - Vote on document structure: sections order, headings, data sources
  - EL-6.AGG equivalent: each plan output validated as valid DocumentPlan JSON
  - Tiebreak: most comprehensive structure wins
  - Rendering itself is ALWAYS deterministic — no voting needed

CONSTRAIN (EL-7.DOC.EVAL):
  - No invented data — EL-7.DOC.RENDER verifies 10 random values
  - No asset_code in document that doesn't exist in assets table
  - eval_score < 0.85 → held_for_review = True, never auto-delivered
  - Audit receipt: plan JSON + data sources + eval score + render_ms
    + spot_checks_run + spot_checks_passed
```

---

### template_fill intent (~5%)

User has an existing template (uploaded or stored) and wants it
populated with real data for a specific asset / period.

```
1. Identify template — from upload or base_templates/
2. EL-7.TEMPLATE.PRE: Parse + validate all {{table.field}} placeholders
   - Every placeholder must resolve to a real table.field
   - SQL fetches values for every placeholder
   - BLOCK if any placeholder unresolvable (no partial fills ever)
3. Fill: python-docx / openpyxl replaces placeholders with real values
4. EL-7.TEMPLATE.POST: Verify filled values
   - No {{...}} strings remain in document
   - Each filled value matches the DB row that sourced it
   - eval_score computed (proportion of values verified)
5. Deliver if eval_score ≥ 0.85, else hold for review
6. Write to document_generation_log with full eval fields
```

Placeholder format: `{{table.field}}` e.g. `{{assets.asset_name}}`,
`{{scheduled_pm.next_due_date}}`, `{{work_orders.status}}`

---

### Output formats (all paths)
| Format | Library | Use case |
|--------|---------|---------|
| `text` | — | Chat answers, summaries |
| `json` | — | API consumers, downstream systems |
| `docx` | python-docx | Reports, templates, inspection forms |
| `xlsx` | openpyxl | Schedules, inventories, data exports |
| `pdf` | reportlab | Final/signed versions of any DOCX |

---

## 15. Entity resolution — 4-tier (shared/entity_resolver.py)

Each tier has an explicit evaluation layer. The result of one tier is only
accepted if it passes evaluation — otherwise it falls to the next tier.

| Tier | Method | Eval Layer | Coverage |
|------|--------|------------|----------|
| 1 — Exact | asset_code or serial_number → Redis cache | EL-ER.T1 | ~70% |
| 2 — Fuzzy | RapidFuzz Levenshtein + trigram (threshold 0.85) | EL-ER.T2 | ~20% |
| 3 — Claude re-query | Unresolved + candidate list → Haiku judges | EL-ER.T3 | ~7% |
| 4 — Manual | Push to review_queue | EL-ER.T4 | ~3% |

```
EL-ER.T1 — Exact Match Output Validation
  - Exactly one record returned (not zero, not multiple)
  - Returned record is active (not retired/deleted)
  - Record type matches expected entity type (asset not user)
  - Cache freshness: if last refresh > 2 hours, flag for re-validation
  PASS → resolved (tier=1, confidence=high)
  FAIL → falls to Tier 2

EL-ER.T2 — Fuzzy Match Output Validation
  - Top candidate score ≥ 0.85
  - Score gap between top and second candidate ≥ 0.10
    (close scores = ambiguous match = fall to Tier 3)
  - Matched entity must belong to same site as the source document
  - Name character length within 30% of source entity name
  PASS → resolved (tier=2, confidence=medium)
  FAIL → falls to Tier 3

EL-ER.T3 — Claude Re-query Output Validation
  - Response is a single valid entity ID string (not free-text)
  - Returned ID exists in current Fiix/CAFM master cache
  - ID type matches expected entity type
  - Returned entity belongs to same site as source document
  - If Claude response contains hedging language → falls to Tier 4
  PASS → resolved (tier=3, confidence=medium)
  FAIL → falls to Tier 4

EL-ER.T4 — Manual Resolution Submission Validation
  - Reviewer has resolve_entities permission (RBAC check)
  - Selected ID exists and is active
  - Not a duplicate simultaneous resolution by two reviewers
  - Resolution written to entity_resolution_cache (future Tier 1 hit)
  - Correction pattern logged to corrections_log
  PASS → resolved (tier=4, manual=true, confidence=high)
  FAIL → entity flagged as unresolvable, work order held
```

Redis cache: 500K assets + 4K users + 1K vendors. Refreshed hourly.
Unit mapping: `C→1, kPa→2, h→3, mm/s→4, A→5, Pa→6`
Date normalisation: all formats → UTC epoch milliseconds.

---

## 16. Infrastructure (live — Azure)

| Component | Details |
|-----------|---------|
| PostgreSQL | `plenum-agentic-ai.postgres.database.azure.com:5432` / db: `plenum_agent` / schema: `plenum_cafm` |
| Redis | `PlenumRedis.uaenorth.redis.azure.net:10000` (TLS — `rediss://`) |
| Azure Blob | Account: `plenumstorage` / Container: `plenum-agentic-ai-attachments` |
| Blob prefixes | `pdf-raw/` · `excel-raw/` · `word-raw/` · `csv-raw/` — all `{tenant}/{yyyy-mm}/{id}` |

### All 13 services running (docker-compose.yml)
| Service | Port | Notes |
|---------|------|-------|
| postgres | 5432 | healthy — `cafm_connectors` DB (local dev only; prod uses Azure PostgreSQL) |
| redis | 6379 | healthy |
| vault | 8200 | up — dev mode, token `cafm-dev-token` |
| tempo | 3200/4317 | healthy — OTLP gRPC traces |
| prometheus | 9090 | healthy |
| grafana | 3000 | up — admin/cafm-dev |
| api (cafm-connector-service) | 8000 | up — `shared-lib` pip-installed at startup |
| worker | — | up — ARQ worker for cafm-connector-service |
| table-editor | 8005 | up — standalone mount at `/table-editor`; `TABLE_EDITOR_STANDALONE_MOUNT=1` |
| svc-ingestion | 8001 | up |
| svc-query | 8002 | up |
| svc-ai-schema-mapper | 8003 | up — LangGraph migration pipeline + schema mapper |
| svc-ai-schema-mapper-worker | — | up — ARQ worker (run_schema_mapping / resume_schema_mapping / run_migration) |
| doc-rag | 8004 (→ inner :8000) | up — Claude PDF RAG; `CLAUDE_PDF_MODEL: claude-sonnet-4-6` |
| svc-ai-schema-mapper-ui | 3001 | up — nginx-served React build |

**Prod Azure URLs (NOT localhost):**
```
PostgreSQL:  plenum-agentic-ai.postgres.database.azure.com:5432 / db: plenum_agent / schema: plenum_cafm
Redis:       PlenumRedis.uaenorth.redis.azure.net:10000 (TLS — rediss://)
Blob:        plenumstorage / container: plenum-agentic-ai-attachments
```

---

## 17. Environment variables

### cafm-connector-service (existing)
```
DB_URL=postgresql+asyncpg://...@plenum-agentic-ai.postgres.database.azure.com:5432/plenum_agent
REDIS_URL=rediss://...@PlenumRedis.uaenorth.redis.azure.net:10000/0
JWT_SECRET=...
SECRETS_BACKEND=env|vault
SECRETS_AES_KEY=<32-byte hex>
AZURE_STORAGE_CONNECTION_STRING=...
AZURE_BLOB_CONTAINER_NAME=plenum-agentic-ai-attachments
```

### svc-ingestion + svc-query + svc-ai-schema-mapper
```
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...           # optional — used for embeddings
LANGSMITH_API_KEY=...        # schema mapper LangSmith tracing
LANGSMITH_TRACING=true
DB_URL=<same PostgreSQL>
REDIS_URL=<same Redis>
AZURE_STORAGE_CONNECTION_STRING=<same>
AZURE_BLOB_CONTAINER_NAME=plenum-agentic-ai-attachments
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
ENVIRONMENT=development|staging|production
DEBUG=false
```

### svc-ai-schema-mapper only (Fiix integration)
```
FIIX_ENABLED=true
FIIX_SUBDOMAIN=...
FIIX_APP_KEY=...
FIIX_ACCESS_KEY=...
FIIX_SECRET_KEY=...
FIIX_TIMEOUT=30
USE_SQLITE_DEV=false          # true = local SQLite instead of PostgreSQL
UPLOAD_DIR=/app/data/doc_rag_uploads
```

### doc-rag
```
ANTHROPIC_API_KEY=...
CLAUDE_PDF_MODEL=claude-sonnet-4-6
DB_URL=postgresql+psycopg://...  # note: psycopg (sync) not asyncpg
USE_SQLITE_DEV=false
APP_ENV=development
LOG_LEVEL=INFO
UPLOAD_DIR=/app/data/uploads
```

---

## 18. Claude SDK reference

### Model selection
| Use case | Model |
|----------|-------|
| PDF/DOCX extraction | `claude-sonnet-4-6` |
| Handwritten/legal docs | `claude-opus-4-6` |
| Schema mapping, Layer 5 agents (all except Inspection) | `claude-haiku-4-5` |
| Inspection agent (nuanced language) | `claude-sonnet-4-6` |
| Layer 6 orchestration | `claude-sonnet-4-6` |
| Intent classification, entity resolution Tier 3 | `claude-haiku-4-5` |
| LLM-as-judge eval (EL-2.3, EL-7.DOC.RENDER) | `claude-haiku-4-5` |
| Batch historical migration | `claude-sonnet-4-6` via Batch API (50% off) |

### PDF methods
```python
# Base64 inline — first time
{'type': 'document', 'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': b64}}

# Files API — re-analysis (upload once, reuse file_id)
# Beta header: 'anthropic-beta: files-api-2025-04-14'
{'type': 'document', 'source': {'type': 'file', 'file_id': 'file_abc123'}}

# Prompt caching
{'type': 'document', ..., 'cache_control': {'type': 'ephemeral'}}
```

---

## 19. Tech stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.12 |
| Web framework | FastAPI + uvicorn |
| AI SDK | `anthropic` |
| Data validation | Pydantic v2 + pydantic-settings |
| ORM | SQLAlchemy 2.0 async (asyncpg) |
| Bulk insert | asyncpg COPY (CSV direct path) |
| Job queue | ARQ over Redis |
| Excel | openpyxl |
| Word/PDF processing | python-docx, pandoc, docx2pdf |
| CSV | pandas (encoding=latin1 for client files) |
| XML | lxml |
| Fuzzy match | RapidFuzz |
| Vector search | pgvector (PostgreSQL extension — no separate DB) |
| Prompt templates | Jinja2 |
| Output rendering | python-docx (Word), reportlab (PDF), openpyxl (XLSX) |
| Secrets | AES-256 / HashiCorp Vault KV v2 |
| Blob | azure-storage-blob |
| Logging | structlog + OTel trace_id correlation |
| Telemetry | OpenTelemetry — full spec in section 22 |
| Testing | pytest + pytest-asyncio |
| Linting | ruff + mypy (strict) |
| Containers | Docker + docker-compose |
| Monitoring | Prometheus + Grafana + Grafana Tempo |

---

## 20. Coding conventions (always follow)

- **Async everywhere** — all DB, HTTP, file I/O must be async/await
- **Pydantic for all schemas** — requests, responses, configs, AgentResult, CMSDecision
- **No hardcoded credentials** — env vars only
- **Structlog** — `get_logger(__name__)`, structured key=value, always inject OTel trace_id
- **UUID primary keys** — all new tables, no integer autoincrement
- **plenum_cafm schema** — all tables live there
- **Never duplicate ORM models** — always import from cafm-connector-service
- **Type hints everywhere** — mypy strict must pass
- **Ruff line length** — 100 characters
- **Extend CAFMError** — use existing exception hierarchy from `core/exceptions.py`
- **3x exponential backoff** — on all Claude API calls; dead letter on permanent failure
- **Shared pipeline first** — Stages 1, 3, 4 written once, reused by all agents
- **Claude called once per file** — never once per row (CSV/Excel)
- **OTel span on every operation** — no function runs without a span
- **Hard rules always beat AI votes** — never let AI override a deterministic fact
- **AgentResult is the contract** — Layer 6 only receives AgentResult objects, never raw data
- **Audit log is INSERT only** — orchestration_audit_log has no UPDATE or DELETE ever
- **N=3 concurrent** — always asyncio.gather for the 3 Claude runs, never sequential
- **Eval layer is not optional** — every agent output passes through eval_layer.py before advancing
- **No output advances without its eval layer passing** — eval is a hard gate, not advisory
- **EL results always logged** — every eval layer result written to the relevant audit table

---

## 21. Sprint 2 task breakdown

### PHASE 1 — Shared foundation ✅ COMPLETE

All Phase 1 tasks are done and live. See section 4 for full status.

---

### PHASE 2 — Ingestion agents + schema mapper + determinism framework

**TASK 2.1 — PDF Agent** | Owner: ML1 | 1.5 days
- `agents/pdf_agent.py`
- Base64 → Claude Vision → intermediate JSON
- Files API, prompt caching, model selection, prompt engine wire-up
- **Must call eval_layer.py (EL-2.1, EL-2.2, EL-2.3) before returning intermediate JSON**
- OTel span: `ingestion.stage2.extract` with pdf-specific attributes
- OTel span: `ingestion.eval.llm_judge` with eval_score attribute

**TASK 2.2 — CSV Agent** | Owner: DE2 | 1 day
- `agents/csv_agent.py`
- `pd.read_csv(encoding='latin1')` for known client files
- Call Layer 3 schema mapper once per file
- **Must call EL-3.0 (mapping confidence check) before proceeding to write**
- Stream 1000-row batches → asyncpg COPY
- **Must call EL-4.0 (pre-write validation) on sample before COPY**
- OTel span: `ingestion.stage2.extract` + `schema_mapper.map`

**TASK 2.3 — DOCX Agent** | Owner: ML2 | 1 day
- `agents/word_agent.py`
- python-docx table scan → Claude Sonnet extraction
- Sections A–G parsing for site inspection report
- **Must call eval_layer.py (EL-2.1, EL-2.2, EL-2.3) before writing to inspections table**
- Dual path: structured extraction + pgvector embedding for SOPs
- INSERT INTO inspections table

**TASK 2.4 — Excel Agent** | Owner: ML1 | 1 day
- `agents/excel_agent.py`
- openpyxl sheet detection + formula resolution
- Layer 3 schema mapper (once per file)
- **Must call EL-3.0 (mapping confidence) + EL-4.0 (pre-write) before COPY**
- asyncpg COPY bulk insert

**TASK 2.5 — XML/JSON Agent** | Owner: DE2 | 1 day
- `agents/xml_json_agent.py`
- lxml XPath / jq JSONPath for known fields
- Haiku only for ambiguous/unmapped fields
- **Must call EL-2.2 (Pydantic schema) before write; EL-2.3 if Claude used**
- JSONL line-by-line streaming

**TASK 2.6 — Layer 3 Schema Mapper** | Owner: ML1 | 0.5 day
- `shared/schema_mapper.py`
- Haiku maps raw headers → canonical field registry
- Redis cache (key: `schema_map:{source_hash}`, TTL: 24h)
- Unmatched → `raw_metadata JSONB`
- **EL-3.0: Flag for human review if mapping confidence < 0.80 — agent blocks until approved**

**TASK 2.7 — Prompt engine** | Owner: ML1 | 1 day
- `prompt_engine/engine.py`
- Jinja2 + Redis caching + hot-reload
- A/B test framework
- All 8 templates (5 PDF + Excel + Word + CSV)

**TASK 2.8 — Batch processor** | Owner: ML1 | 1 day
- `batch/batch_processor.py`
- Claude Batch API JSONL (up to 10,000 requests)
- **Each batch result independently runs EL-2.1, EL-2.2, EL-2.3 before processing**
- **If > 20% of results fail EL-2.x → entire batch flagged, ops notified**
- Poll → process results through Stage 3+4
- WebSocket progress

**TASK 2.9 — Entity resolver (full 4-tier)** | Owner: DE2 | 1 day
- `shared/entity_resolver.py`
- Redis warm-up: 500K assets + 4K users + 1K vendors
- Tier 1 exact → EL-ER.T1 → Tier 2 RapidFuzz → EL-ER.T2 → Tier 3 Haiku → EL-ER.T3 → Tier 4 manual → EL-ER.T4
- **Every tier output evaluated before acceptance — see section 15**
- Date normaliser + unit mapper

**TASK 2.10 — Alembic migration 002** | Owner: DE2 | 0.5 day
- Add `inspections` table (section 8)
- Add `agent_audit_log` table with eval result columns (section 13)
- Add `orchestration_audit_log` table with eval result columns (section 13)
- Apply to live Azure PostgreSQL

---

### PHASE 3 — Eval layer + review queue

**TASK 3.1 — LLM-as-judge eval (EL-2.3)** | Owner: ML2 | 0.5 day
- `shared/eval_layer.py`
- Haiku reviews extraction vs source — returns eval_score (0–1)
- **This is EL-2.3 — called by every ingestion agent that uses Claude**
- Stored in intermediate JSON `confidence.eval_score`
- OTel span: `ingestion.eval.llm_judge` with eval_score, rules_passed attributes

**TASK 3.2 — Rule engine (part of EL-2.3)** | Owner: ML2 | 0.5 day
- Within `shared/eval_layer.py`
- YAML-configurable contradiction rules
- **Runs as part of EL-2.3 after LLM-as-judge score**
- New rules without code changes

**TASK 3.3 — Multi-pass voting for ingestion (part of EL-2.3)** | Owner: ML1 | 0.5 day
- Within `agents/pdf_agent.py`
- 3 passes for ComplianceCert + legal docs
- **Field accepted as `high` confidence only if all 3 agree — this IS the eval for these doc types**

**TASK 3.4 — Review queue** | Owner: ML2 | 1 day
- `review_queue/queue.py` + `review_queue/websocket.py`
- Redis sorted set, 10-min reviewer lock
- API: GET/POST /review endpoints
- **Receives items routed from EL-2.3 (eval_score 0.60-0.84) and EL-3.0 (mapping < 0.80)**
- Corrections → `corrections_log`

**TASK 3.5 — Prompt refinement loop** | Owner: ML2 | 1 day
- Weekly: aggregate correction patterns → suggest prompt edits
- Auto A/B test on approved changes

---

### PHASE 4 — Layer 5 specialist data agents + determinism framework

**TASK 4.1 — AgentResult model + determinism wrapper** | Owner: ML1 | 1 day
- `data_agents/base_data_agent.py` — AgentResult Pydantic model
- `shared/agent_determinism.py` — reusable Bound→Aggregate→Constrain class
- **EL-5.BOUND, EL-5.AGG, EL-5.VOTE, EL-5.CONSTRAIN all implemented here**
- `asyncio.gather` for concurrent N=3 runs
- Agent-level audit log writer (writes bound_validation_passed, run_N_valid, confidence_gate_passed)

**TASK 4.2 — YAML safety rules per agent** | Owner: ML2 | 0.5 day
- `data_agents/rules/asset_rules.yaml`
- `data_agents/rules/wo_rules.yaml`
- `data_agents/rules/pm_rules.yaml`
- `data_agents/rules/parts_rules.yaml`
- `data_agents/rules/inspection_rules.yaml`
- **These are the hard overrides in EL-5.CONSTRAIN — must be complete before agents build**

**TASK 4.3 — Asset Agent** | Owner: ML1 | 0.5 day
- `data_agents/asset_agent.py`
- Uses `AgentDeterminismCycle` — inherits all EL-5.x layers automatically

**TASK 4.4 — WO Agent** | Owner: ML1 | 0.5 day
- `data_agents/wo_agent.py`
- Uses `AgentDeterminismCycle` — inherits all EL-5.x layers automatically

**TASK 4.5 — PM Agent** | Owner: ML2 | 0.5 day
- `data_agents/pm_agent.py`
- Uses `AgentDeterminismCycle` — inherits all EL-5.x layers automatically

**TASK 4.6 — Parts Agent** | Owner: DE2 | 0.5 day
- `data_agents/parts_agent.py`
- Uses `AgentDeterminismCycle` — inherits all EL-5.x layers automatically

**TASK 4.7 — Inspection Agent** | Owner: ML2 | 0.5 day
- `data_agents/inspection_agent.py`
- Uses `AgentDeterminismCycle` — inherits all EL-5.x layers automatically

---

### PHASE 5 — Layer 6 + svc-query

**TASK 5.1 — Layer 6 orchestrator** | Owner: ML1 | 1.5 days
- `analysis/orchestrator.py`
- Collects 5 AgentResults, runs Bound→Aggregate→Constrain
- **EL-6.BOUND, EL-6.AGG, EL-6.VOTE, EL-6.CONSTRAIN all in this file**
- Writes to orchestration_audit_log (with all eval result columns)

**TASK 5.2 — Intent classifier** | Owner: ML2 | 0.5 day
- `intent_classifier.py`
- Haiku, < 500ms, 5 intent types
- confidence < 0.80 → clarifying question

**TASK 5.3 — Tier 1 ICC chat** | Owner: DE2 | 1 day
- `tiers/structured_query.py`
- Parameterised SQL, read replica, grounded answer
- **EL-7.QUERY: answer verified to only use values from SQL result set**

**TASK 5.4 — Tier 2 fetch** | Owner: DE2 | 0.5 day
- `tiers/fetch_then_read.py`
- **EL-7.QUERY: answer verified to only use values from fetched document**

**TASK 5.5 — Tier 3 vector** | Owner: ML1 | 0.5 day
- `tiers/vector_search.py`
- **EL-7.QUERY: answer verified to only cite retrieved chunks**

**TASK 5.6 — Doc planner** | Owner: ML1 | 1 day
- `document_generator/planner.py`
- N=3 Sonnet, vote on DocumentPlan structure
- Each run output validated as valid DocumentPlan JSON (EL-6.AGG equivalent)

**TASK 5.7 — Doc renderer** | Owner: ML1 | 1 day
- `document_generator/renderer.py`
- Deterministic — no Claude — python-docx / openpyxl / reportlab

**TASK 5.8 — Doc validator (EL-7.DOC.PLAN)** | Owner: ML2 | 0.5 day
- `document_generator/validator.py`
- **This IS EL-7.DOC.PLAN — Pydantic + data source dry-run queries**
- Blocks render if any data_source unresolvable

**TASK 5.9 — Doc eval layer (EL-7.DOC.RENDER + EL-7.DOC.EVAL)** | Owner: ML2 | 1 day
- `svc-query/src/eval_layer.py`
- **This IS EL-7.DOC.RENDER and EL-7.DOC.EVAL**
- Haiku spot-checks 10 random values
- eval_score < 0.85 → held_for_review = True
- Writes spot_checks_run, spot_checks_passed, eval_score to document_generation_log

**TASK 5.10 — Template filler (EL-7.TEMPLATE)** | Owner: ML2 | 0.5 day
- `document_generator/filler.py`
- **Pre-fill: EL-7.TEMPLATE.PRE — all placeholders must resolve before fill**
- **Post-fill: EL-7.TEMPLATE.POST — verify all placeholders replaced + values match DB**

**TASK 5.11 — Base templates** | Owner: ML2 | 0.5 day
- 7 DOCX/XLSX templates in `base_templates/`

**TASK 5.12 — Output renderer** | Owner: DE2 | 0.5 day
- `output_renderer.py`
- text / json / word / pdf / xlsx dispatch

---

### PHASE 6 — Integration + performance

**TASK 6.1 — Dashboards** | Owner: DE2 | 1 day
- 6 Grafana dashboards (see section 24)
- **Dashboard 1 must include eval_score distribution + held_for_review rate**
- **Dashboard 4 must include EL-5.x pass rates per agent**

**TASK 6.2 — E2E tests** | Owner: QA | 2 days
- CSV + DOCX + PDF → query → CMSDecision → document
- **Must include eval layer pass/fail scenarios for every EL-x.x**
- **Must verify held_for_review=True fires correctly when eval_score < 0.85**

**TASK 6.3 — Perf test** | Owner: QA | 1 day
- P95 latency targets

**TASK 6.4 — Demo** | Owner: PM | 0.5 day
- Live: CSV + DOCX + PDF → query → CMSDecision → auto-generated PM schedule

---

## 22. Evaluation layer quick reference

Summary of every eval layer, its file location, what it checks, and pass/fail actions.

| ID | File | Trigger | Checks | Pass | Fail |
|----|------|---------|--------|------|------|
| EL-2.0 | `shared/ingest.py` | Every file upload | Type, size, hash, duplicate | Proceed | Reject + log |
| EL-2.1 | `shared/eval_layer.py` | Every Claude extraction response | Valid JSON, required keys | → EL-2.2 | Retry ×3 |
| EL-2.2 | `shared/eval_layer.py` | After EL-2.1 | Pydantic schema, per_field tags, no null IDs | → EL-2.3 | Review queue |
| EL-2.3 | `shared/eval_layer.py` | After EL-2.2 | LLM-as-judge eval_score + YAML rules | Route by score | Re-extract / review |
| EL-3.0 | `shared/schema_mapper.py` | After schema mapping | Mapping confidence ≥ 0.80 | Proceed | Block + review |
| EL-4.0 | `shared/unifier.py` | Before every DB write | Pydantic, FK, enums, ranges | Write | Skip row + log |
| EL-5.BOUND | `shared/agent_determinism.py` | Before aggregate | Row types, nulls, enums, ranges | Proceed | BoundValidationError |
| EL-5.AGG | `shared/agent_determinism.py` | Each of N=3 runs | Status enum, confidence range, word limit | Count in vote | Exclude run |
| EL-5.VOTE | `shared/agent_determinism.py` | After aggregate | Majority exists, ≥2 valid runs | Winner selected | human_review=True |
| EL-5.CONSTRAIN | `shared/agent_determinism.py` | After vote | Hard rules + confidence gate | AgentResult | human_review=True |
| EL-ER.T1 | `shared/entity_resolver.py` | Tier 1 result | Unique, active, correct type, fresh | resolved (tier=1) | → Tier 2 |
| EL-ER.T2 | `shared/entity_resolver.py` | Tier 2 result | Score ≥ 0.85, gap ≥ 0.10, site match | resolved (tier=2) | → Tier 3 |
| EL-ER.T3 | `shared/entity_resolver.py` | Tier 3 result | Single ID, exists, type match, no hedging | resolved (tier=3) | → Tier 4 |
| EL-ER.T4 | `shared/entity_resolver.py` | Tier 4 submission | Permissions, ID active, no race | resolved (manual) | unresolvable |
| EL-6.BOUND | `analysis/orchestrator.py` | Before L6 aggregate | 5 AgentResults, no human_review | Proceed | human_review action |
| EL-6.AGG | `analysis/orchestrator.py` | Each of N=3 runs | Action enum, confidence, word limit | Count in vote | Exclude run |
| EL-6.VOTE | `analysis/orchestrator.py` | After aggregate | Majority exists, ≥2 valid runs | Winner selected | human_review action |
| EL-6.CONSTRAIN | `analysis/orchestrator.py` | After vote | Confidence ≥ 0.85, safety gates | CMSDecision | human_review action |
| EL-7.QUERY | `tiers/*.py` | Every query answer | Values traceable to SQL/doc/chunk | Deliver | "No data found" |
| EL-7.DOC.PLAN | `document_generator/validator.py` | After plan vote | Pydantic, data sources resolve, rows exist | Render | Re-plan ×1 → error |
| EL-7.DOC.RENDER | `svc-query/src/eval_layer.py` | After render | 10 spot-checks vs DB | → EL-7.DOC.EVAL | held_for_review |
| EL-7.DOC.EVAL | `svc-query/src/eval_layer.py` | After spot-checks | eval_score ≥ 0.85 | Deliver | held_for_review=True |
| EL-7.TEMPLATE.PRE | `document_generator/filler.py` | Before template fill | All placeholders resolve | Fill | Block |
| EL-7.TEMPLATE.POST | `document_generator/filler.py` | After template fill | No unfilled {{...}}, values match DB | Deliver | held_for_review |

---

## 23. Definition of done

| # | Criterion |
|---|-----------|
| 1 | All ingestion agents produce valid intermediate JSON |
| 2 | All agents write to same plenum_cafm tables via shared unifier |
| 3 | Layer 3 schema mapper resolves all 6 known CSV files correctly |
| 4 | `inspections` table populated from both DOCX and PDF agents |
| 5 | All 5 data agents run their own Bound→Aggregate→Constrain cycle |
| 6 | Hard rules verified: stock=0→critical, Highest+7d→escalate, date math wins |
| 7 | Layer 6 majority vote correct on 100 test scenarios |
| 8 | Confidence gate (0.85) and alert_critical→human_review both enforced |
| 9 | orchestration_audit_log is INSERT only — no UPDATE or DELETE possible |
| 10 | agent_audit_log captures all 3 runs + eval results for every agent invocation |
| 11 | Review queue functional: routing, HITL, correction logging, re-extract |
| 12 | Accuracy targets met on 200 ground-truth documents |
| 13 | ICC chat returns SQL-grounded answers (no hallucination) |
| 14 | DOCX Section F auto-filled with AI decision block |
| 15 | XLSX parts alert column shows ORDER NOW / REORDER SOON correctly |
| 16 | Work order auto-raised when create_wo + confidence ≥ 0.85 |
| 17 | Intent classifier correctly routes all 5 intent types in test suite |
| 18 | DocumentPlan validator (EL-7.DOC.PLAN) rejects plans with unresolvable data sources |
| 19 | Document renderer produces valid DOCX/XLSX/PDF for all 7 document types |
| 20 | EL-7.DOC.EVAL: every value in generated doc traces to real DB row |
| 21 | EL-7.DOC.EVAL: documents with eval_score < 0.85 held for review — never auto-delivered |
| 22 | EL-7.TEMPLATE: template filler resolves all {{table.field}} placeholders correctly |
| 23 | document_generation_log captures every generation event with full plan JSON + eval fields |
| 24 | EL-2.3 (LLM-as-judge) running on all PDF and DOCX agent outputs |
| 25 | EL-5.BOUND firing BoundValidationError correctly on bad rows in all 5 agents |
| 26 | EL-ER.T1 through EL-ER.T4 all tested and tier fallthrough verified |
| 27 | Eval layer quick reference table (section 22) matches implemented behaviour |
| 28 | Cost dashboard live — per-agent, per-model, budget burn |
| 29 | P95 latency within targets |
| 30 | Zero P1/P2 defects |
| 31 | Live demo: CSV + DOCX + PDF → query → CMSDecision → auto-generated PM schedule |

---

## 24. Observability (OpenTelemetry — mandatory everywhere)

**Already live:** Tempo, Prometheus, Grafana all running. All 3 services
instrumented with `configure_telemetry()`. Every log line has trace_id.

**Additional spans for Phase 2+ (add to existing spans):**

```
# Layer 3 schema mapper
schema_mapper.map
  cafm.source_hash, cafm.headers_count, cafm.mapped_count,
  cafm.unmatched_count, cafm.cache_hit (bool)

# Eval layer — ingestion (EL-2.x)
ingestion.eval.pre_validation        ← EL-2.0
  cafm.file_type, cafm.file_size_mb, cafm.duplicate (bool)

ingestion.eval.extraction_output     ← EL-2.1
  cafm.agent_id, cafm.json_valid (bool), cafm.retry_count

ingestion.eval.schema_conformance    ← EL-2.2
  cafm.agent_id, cafm.entities_count, cafm.schema_valid (bool)

ingestion.eval.llm_judge             ← EL-2.3
  cafm.agent_id, cafm.eval_score, cafm.rules_violations_count,
  cafm.route (accept|review|re_extract)

# Eval layer — schema mapper (EL-3.0)
schema_mapper.eval
  cafm.mapping_confidence, cafm.human_review_required (bool)

# Eval layer — entity resolver (EL-ER.x)
entity_resolver.tier1_eval
  cafm.entity_type, cafm.match_unique (bool), cafm.record_active (bool)

entity_resolver.tier2_eval
  cafm.top_score, cafm.score_gap, cafm.site_match (bool)

entity_resolver.tier3_eval
  cafm.response_valid (bool), cafm.id_exists (bool)

entity_resolver.tier4_eval
  cafm.reviewer_authorized (bool), cafm.resolved (bool)

# Layer 5 data agents (per agent)
data_agent.bound                     ← EL-5.BOUND
  cafm.agent_id, cafm.rows_in, cafm.rows_valid, cafm.rows_rejected

data_agent.aggregate                 ← EL-5.AGG
  cafm.agent_id, cafm.model, cafm.n_runs,
  cafm.runs_valid, cafm.runs_agreed, cafm.winner_status, cafm.avg_confidence

data_agent.constrain                 ← EL-5.CONSTRAIN
  cafm.agent_id, cafm.hard_rules_fired_count,
  cafm.confidence_gate_passed (bool), cafm.requires_human_review

# Layer 6
orchestrator.bound                   ← EL-6.BOUND
  cafm.agent_results_count, cafm.any_requires_human_review,
  cafm.bound_passed (bool)

orchestrator.aggregate               ← EL-6.AGG
  cafm.model, cafm.n_runs, cafm.runs_valid,
  cafm.runs_agreed, cafm.winner_action, cafm.avg_confidence

orchestrator.constrain               ← EL-6.CONSTRAIN
  cafm.action, cafm.confidence, cafm.confidence_gate_passed,
  cafm.safety_rules_fired

# Document generation (svc-query)
document.classify_intent
  cafm.query_id, cafm.intent_type, cafm.document_type_detected

document.plan
  cafm.query_id, cafm.document_type, cafm.sections_count,
  cafm.plan_runs_agreed, cafm.model, claude.tokens_in, claude.tokens_out

document.validate                    ← EL-7.DOC.PLAN
  cafm.query_id, cafm.data_sources_count,
  cafm.all_sources_resolved (bool), cafm.validation_passed (bool)

document.render
  cafm.query_id, cafm.document_type, cafm.output_format,
  cafm.sections_rendered, cafm.render_ms

document.eval                        ← EL-7.DOC.RENDER + EL-7.DOC.EVAL
  cafm.query_id, cafm.values_spot_checked, cafm.values_verified,
  cafm.eval_score, cafm.held_for_review (bool)

template.fill                        ← EL-7.TEMPLATE
  cafm.query_id, cafm.template_name, cafm.placeholders_total,
  cafm.placeholders_resolved, cafm.placeholders_missing,
  cafm.post_fill_eval_score
```

**Six Grafana dashboards required:**
1. Ingestion Overview — docs/hr, duration P95, confidence dist, eval_scores (EL-2.3), queue depth, held_for_review rate
2. Claude API + Cost — calls/hr, tokens, cost/day, budget burn, cache hit rate
3. Entity Resolution — tier distribution, fuzzy scores, EL-ER eval pass rates, unresolved trend
4. Layer 5 + Layer 6 — agent run times, EL-5.BOUND pass rates, votes agreed, hard rules fired, action distribution, EL-6.CONSTRAIN gate results
5. Document Generation — requests/hr by type, render time P95, EL-7 eval scores, held_for_review rate
6. Service Health — HTTP rates, DB pool, Redis, ARQ worker throughput

---

## 25. What we are explicitly NOT doing

- ❌ No Tesseract OCR
- ❌ No LayoutLM, SpaCy NER, or fine-tuned ML models
- ❌ No separate vector DB — pgvector on existing PostgreSQL only
- ❌ No vector search for structured data (Tier 1/2 queries)
- ❌ No rewriting cafm-connector-service — extend only
- ❌ No duplicating ORM models — always import from cafm-connector-service (now 50 models)
- ❌ No raw table/column names in SQL — always validate against `_SAFE_IDENT` regex + `information_schema` allow-list (table_customizer.py pattern)
- ❌ No DDL without `?confirm=true` — add/drop column endpoints require explicit confirmation param
- ❌ No integer primary keys on new tables
- ❌ No synchronous DB or HTTP calls
- ❌ No raw LLM SQL executed directly — always parameterised
- ❌ No LLM called per CSV/Excel row — schema mapping once per file
- ❌ No AI overriding hard rules — YAML rules always take precedence
- ❌ No raw data reaching Layer 6 — only typed AgentResult objects
- ❌ No UPDATE or DELETE on orchestration_audit_log — INSERT only forever
- ❌ No sequential N=3 runs — always asyncio.gather (concurrent)
- ❌ No uninstrumented code — every function has an OTel span
- ❌ No telemetry as afterthought — Task 1.0 already shipped
- ❌ No Claude generating document files directly — planner produces JSON plan only
- ❌ No invented values in generated documents — every value must trace to a DB row
- ❌ No auto-delivery of documents with eval_score < 0.85 — always held for review
- ❌ No free-form document generation — always DocumentPlan JSON → deterministic renderer
- ❌ No template placeholders left unfilled — EL-7.TEMPLATE.PRE blocks render if any unresolved
- ❌ No agent output advancing without its evaluation layer — eval is a hard gate everywhere
- ❌ No eval layer result going unlogged — every EL-x.x result written to its audit table
- ❌ No entity resolution result accepted without its tier eval layer passing