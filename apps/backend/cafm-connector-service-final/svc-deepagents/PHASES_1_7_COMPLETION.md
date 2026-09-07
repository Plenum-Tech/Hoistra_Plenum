# Phases 1–7 — Completion Status

**Canonical front door:** `svc-deepagents` (`/api/workflow/*`)  
**Primary UI:** `apps/frontend/src/features/ai/pipeline/deep-agent/`

| Phase | Goal | Status | Key files |
|-------|------|--------|-----------|
| **1** | Single Door contract + route intents + metadata | ✅ | `session_workspace.py`, `system_prompt.py`, `workflow.py` (`route_metadata`) |
| **2** | UDR ingest → map/hierarchy gates + session state | ✅ | `session_workspace.py`, `orchestrator.py` |
| **3** | WO semantic clarification (confidence bands) | ✅ | `orchestrator.py`, `session_workspace.py` |
| **4** | Multi-file / multi-format one-door ingest | ✅ | `workflow.py`, `single_door_flow.py`, `deep-agents-api.ts`, orchestrator shell |
| **5** | Hybrid UDR (structured + vector + links) | ✅ | `udr_hybrid_tools.py`, `udr_agent.py` (HTTP + DB) |
| **6** | Cloud connector registry | ✅ | `integrations/source_connectors.py`, `connector_tools.py`, `config.py` |
| **7** | UX status cards + workspace API | ✅ | `GET /api/workflow/workspace/{id}`, `deep-agent-workspace-status.tsx` |

**Also completed (Phase D):** Live Fiix, image/scan ingest, bulk batch jobs — see `PHASE_D_PLAN.md`.

---

## Phase 1 — Single Door Contract

- **Policy** in `system_prompt.py` (Single Door Policy section).
- **Route intents:** `udr_ingest_documents`, `udr_run_mapping_hierarchy`, `wo_intake_or_create`, `wo_clarify_candidate`, `general_query` (+ `fiix_sync`, `bulk_ingest`).
- **Response metadata:** `route_metadata` on every `WorkflowResponse` (`route_intent`, `selected_domain`, `selected_tool`, `next_step_prompt`).

---

## Phase 2 — UDR Workflow Gates

- **No ingestion** → guided message (upload / Fiix).
- **Ingested, mapping incomplete** → guided next step (run migration mapping).
- **Session state:** `documents_ingested_count`, `pending_batch_ids`, `mapping_status`, `hierarchy_status`, `active_batch_id`, `fiix_ingestion_id`.
- **API:** `GET /api/workflow/workspace/{session_id}`.

---

## Phase 3 — WO Semantic Clarification

- **Confidence:** `high` / `medium` → confirmation question; `low` → ask user to classify.
- **Flow:** `pending_wo_clarification` → yes → `prepare_intelligent_work_order`.

---

## Phase 4 — Multi-file Ingestion

- `POST /api/workflow/run-stateful-with-files` (CSV/Excel/PDF/Word/images).
- `ingest_source=fiix` for live CMMS.
- Bulk path when file count > `INGEST_BATCH_INLINE_THRESHOLD` (default 3).

---

## Phase 5 — Hybrid UDR

| Tool | Role |
|------|------|
| `retrieve_workspace_corpus_summary` | Workspace + schema snapshot |
| `retrieve_vector_evidence` | Document chunks (semantic) |
| `resolve_cross_source_links` | Structured sample + RAG answer |
| `udr_*` / `query_table` / `udr_agent_query` | Structured register |

---

## Phase 6 — Cloud Connectors

| `source_type` | Strategy |
|---------------|----------|
| `file_upload` | Single-door multipart |
| `fiix` | Live API via schema-mapper |
| `azure_blob` | Object storage (env-gated stub) |
| `tencent_cos` | Object storage (env-gated stub) |

Tools: `list_available_source_connectors`, `test_source_connector_connection`.

LLM routing (Azure / Tencent / OpenAI): `llm_factory.py` (enterprise Phase 6 complement).

---

## Phase 7 — UX Harmonization

- Deep Agent shell uses **only** `deep-agents-api` → `/api/workflow/*`.
- **Status pills:** Ingestion, Mapping, Hierarchy, WO candidate (+ batch id).
- Polls workspace every 8s.

---

## Smoke tests

```powershell
$G = "http://127.0.0.1:3000/backend/deep-agents"

# Workspace status
curl -sS "$G/api/workflow/workspace/your-session-uuid"

# UDR gate (no ingest)
curl -sS -X POST "$G/api/workflow/run-stateful" -H "Content-Type: application/json" `
  -d '{"message":"run udr mapping now","session_id":"phase1-smoke-001"}'

# WO clarification
curl -sS -X POST "$G/api/workflow/run-stateful" -H "Content-Type: application/json" `
  -d '{"message":"Chiller tripping urgent at Tower A","session_id":"phase3-smoke-001"}'

# Connectors list (via chat or tools endpoint)
curl -sS "$G/api/workflow/tools" | findstr list_available_source_connectors
```

**Unit tests:** `pytest tests/test_phases_1_7.py tests/test_phase_d.py`

---

## Out of scope / future

- Full Azure/Tencent **object ingest drivers** (connectors registered; upload path is file/Fiix today).
- Duplicate orchestrators in `svc-udr` / `svc-work-order-management` — still internal; users should use deepagents only.
- Formal `svc-udr` agent prompt changes (hybrid tools live in deepagents first).
