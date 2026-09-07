# svc-deepagents — Architecture

**Service:** `svc-deepagents` — DeepAgents Orchestration Layer  
**Port:** 8008  
**Model:** `gpt-4o-mini` (OpenAI) via LangChain `init_chat_model`  
**Framework:** LangGraph `create_react_agent` + LangChain tools  
**Date:** 2026-05-14

---

## 1. Overview

`svc-deepagents` is the main AI orchestration service for the Plenum CAFM platform.
It sits at the top of the AI layer and acts as the single entry point for all natural
language requests from the frontend and other services.

The orchestrator receives a plain English request, reasons over which tools to use,
calls them in the correct sequence, and returns a grounded, human-readable answer
along with the full tool call trace for audit and debugging.

It does not perform any business logic itself — it delegates every operation to one
of 5 specialised agent domains, each wired to the appropriate downstream service.
Six additional meta-capability tools handle orchestration itself: planning, subagent
spawning, context offload, and session memory.

---

## 2. High-level architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          React Frontend (port 3001)                     │
│                    svc-ai-schema-mapper-ui + chat panel                 │
└────────────────────────────┬────────────────────────────────────────────┘
                             │  POST /api/workflow/run
                             │  POST /api/workflow/run-stateful
                             │  POST /api/workflow/resume/{session_id}
                             │  GET  /api/workflow/status/{session_id}
                             │  WS   /api/workflow/ws/{session_id}
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    svc-deepagents  (port 8008)                          │
│                                                                         │
│   FastAPI app                                                           │
│   ├── POST /api/workflow/run            ← one-shot stateless            │
│   ├── POST /api/workflow/run-stateful   ← HITL-capable (Postgres ckpt) │
│   ├── POST /api/workflow/resume/{sid}   ← submit human decision        │
│   ├── GET  /api/workflow/status/{sid}   ← interrupt status check       │
│   ├── WS   /api/workflow/ws/{sid}       ← real-time event streaming    │
│   └── GET  /health                      ← liveness probe               │
│                                                                         │
│   DeepAgentOrchestrator                                                 │
│   ├── LLM: gpt-4o-mini (temperature=0, deterministic)                       │
│   ├── Framework: LangGraph create_react_agent                          │
│   ├── Checkpointer: AsyncPostgresSaver (HITL mode)                     │
│   ├── HTTP client: shared retry + circuit breaker (http_client.py)     │
│   ├── System prompt: CAFM domain context + 46-tool catalogue           │
│   └── Tool registry: 46 tools across 6 groups                         │
└──────┬──────────┬──────────┬──────────┬──────────┬──────────┬──────────┘
       │          │          │          │          │          │
    ┌──┘   ┌──────┘   ┌──────┘   ┌──────┘   ┌──────┘   ┌──────┘
    ▼      ▼          ▼          ▼          ▼          ▼
┌──────┐ ┌──────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────────┐
│ Meta │ │ UDR  │ │   WO   │ │ Migr.  │ │  Doc   │ │ Compliance │
│(6t)  │ │(3t)  │ │ Engine │ │ Agent  │ │  RAG   │ │  Agent     │
│      │ │      │ │(21t)   │ │(8t)    │ │(6t)    │ │(2t)        │
└──────┘ └──┬───┘ └───┬────┘ └───┬────┘ └───┬────┘ └─────┬──────┘
            │          │          │           │            │
            ▼          ▼          ▼           ▼            ▼
       ┌────────┐ ┌──────────┐ ┌────────┐ ┌────────┐ ┌────────────┐
       │Azure   │ │svc-work- │ │svc-ai- │ │doc-rag │ │ Azure      │
       │Postgres│ │order-mgmt│ │schema- │ │(port   │ │ Postgres   │
       │(direct │ │  :8007   │ │mapper  │ │ 8004)  │ │ (direct    │
       │  SQL)  │ │          │ │(:8003) │ │        │ │   SQL)     │
       └────────┘ └──────────┘ └────────┘ └────────┘ └────────────┘
```

---

## 3. Request lifecycle

### Stateless (POST /api/workflow/run)

```
1. HTTP request arrives at FastAPI
   └── WorkflowRequest validated (Pydantic): message, session_id, context

2. get_orchestrator() dependency injects singleton DeepAgentOrchestrator

3. orchestrator.run() called
   ├── Fresh UUID thread_id (stateless — no history leaks)
   ├── set_session_context(thread_id) → namespaces file/memory writes
   ├── build_system_prompt(extra_context) → constructs full prompt
   └── LangGraph agent: ainvoke([SystemMessage, HumanMessage])
       ┌─────────────────────────────────────────────────┐
       │ THINK: gpt-4o-mini reasons about the request         │
       │ ACT:   selects a tool and builds its arguments  │
       │ OBSERVE: tool executes, result returned to LLM  │
       │ (repeats until answer is complete)              │
       └─────────────────────────────────────────────────┘

4. Tool call trace extracted from messages → [{tool, input, output}]
5. Final answer extracted (last AIMessage with no pending tool_calls)
6. WorkflowResponse returned: session_id, answer, tool_calls, success
```

### HITL-capable (POST /api/workflow/run-stateful)

Same as above but `session_id` is the LangGraph `thread_id`. State is persisted
to Postgres via `AsyncPostgresSaver`. If a tool calls `interrupt()`, the response
returns `interrupted=true` + `interrupt_payload`. The client then calls
`POST /api/workflow/resume/{session_id}` with the human decision to continue.

### WebSocket streaming (WS /api/workflow/ws/{session_id})

Client connects and sends `{"message": "...", "context": "..."}`. The orchestrator
calls `astream_events(version="v2")` and emits typed JSON frames as events arrive:

```
tool_started       → a tool call is beginning
tool_completed     → tool call finished
agent_switch       → domain changed between consecutive tools
gate_interrupt     → HITL interrupt() fired
workflow_completed → final answer ready
error              → unexpected exception
```

---

## 4. The 6 tool groups

### 4.0 Meta-Capability Tools (6 tools) — `src/agents/meta_tools.py`

**Purpose:** Orchestration primitives — planning, subagent delegation, context offload,
and session memory. These are the tools that make the other 32 tools composable.

**Implementation:** In-process (no HTTP hop). Session-isolated via `ContextVar`.

| Tool | Description |
|------|-------------|
| `write_todos(todos)` | Record an explicit step-by-step plan. Use before any multi-step task (≥ 3 steps or ≥ 2 domains). |
| `task(agent, prompt)` | Spawn a focused subagent (`migration\|doc_rag\|wo_engine\|compliance\|udr`). Runs with only that domain's tools. Returns result string. |
| `write_file(path, content)` | Write data to a session-scoped temp file. Prevents context overflow when tool results exceed ~50 records. |
| `read_file(path)` | Read previously written session data back into context. |
| `memory_set(key, value)` | Persist a key/value pair for the current session (site, user role, last asset). |
| `memory_get(key)` | Retrieve a persisted session value. |

**Session isolation:** All file writes (`write_file`) and memory entries (`memory_set`)
are keyed under the current `thread_id` via a `ContextVar`. No data leaks between requests.

**Subagent HITL note:** `task()` sub-agents run without a checkpointer — HITL gates
inside `map_fields` or `rollback_migration` are bypassed when invoked via `task()`.
For HITL-aware migration flows, callers must use `POST /run-stateful` at the top level.

---

### 4.1 UDR Agent — Universal Database Reader (2 tools)

**Purpose:** Direct SQL access to any table in the `plenum_cafm` schema.
Used as the fallback when no specific tool covers the user's data need.

**Implementation:** Direct `AsyncSessionLocal` (SQLAlchemy asyncpg) — no HTTP hop.

**Security model:**
- Table and column names validated against `_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")`
- All filter values passed as SQLAlchemy `text()` named parameters — no string interpolation
- Result set hard-capped at 100 rows

| Tool | Description |
|------|-------------|
| `lookup_user(user_id)` | Joins users + user_roles + roles; returns name/email/department/phone/roles[] |
| `query_table(table_name, filters)` | Generic SELECT with optional equality filters, 100-row cap |

---

### 4.2 WO Engine Agent — Work Order Lifecycle (21 tools)

**Purpose:** Full work order and maintenance management — dynamic multi-step approval,
intelligent AI pipeline, CRUD lifecycle, asset/location reference lookups, PPM scheduling,
email intake, and dashboard statistics.

**Implementation:** HTTP calls to `svc-work-order-management` (port 8007) via shared
`http_client.request()` (3 retries, exponential backoff, per-service circuit breaker).

**Dynamic approval (5 tools)** — rule + historical similarity scoring via
`DynamicApprovalEngine`; proxied to `POST /api/work-orders/suggest-approval`,
`POST /{id}/request-approval`, `GET /{id}/approval-chain`, `PATCH /{id}/customize-chain`,
`POST /api/work-orders/approvals/{id}/respond`. Orchestrator must call
create WO first, then surface `auto_suggestion` from the create response (or `suggest_approval_chain` with `work_order_id` to refresh).

| Tool | Description |
|------|-------------|
| `suggest_approval_chain(...)` | Preview chain + `previous_approval_processes` |
| `request_approval_chain(work_order_id)` | Persist chain and notify step 1 |
| `get_approval_chain(work_order_id)` | All steps and statuses |
| `customize_approval_chain(work_order_id, chain)` | Override pending approvers |
| `respond_to_approval_step(approval_request_id, approved)` | Approve/reject one step |

**Intelligent pipeline (3 tools) — run the full 15-step AI assessment:**

| Tool | Description |
|------|-------------|
| `create_intelligent_work_order(...)` | Primary WO creation — runs full 15-step AI assessment via POST /api/chat/ |
| `trigger_ppm_work_order(schedule_id, asset_id, asset_name, description)` | Trigger a due PPM schedule through the AI agent |
| `process_email_work_order(subject, body, sender_name, sender_email)` | Process incoming maintenance email into a WO |

**CRUD + lifecycle (8 tools):**

| Tool | Description |
|------|-------------|
| `create_work_order(...)` | Direct WO creation without AI assessment |
| `get_work_order(work_order_id)` | Full WO detail: status, priority, asset, vendor, schedule |
| `update_work_order(work_order_id, ...)` | Update editable fields — does not change status |
| `list_work_orders(status, priority, source, asset, from_date, to_date)` | List WOs with optional filters |
| `transition_work_order(work_order_id, new_status, notes)` | State machine transitions |
| `approve_work_order(work_order_id)` | Approve pending_approval → preparing |
| `close_work_order(work_order_id, notes)` | Close from any open status (terminal) |
| `get_work_order_history(work_order_id)` | Chronological status change log |

**Reference lookups (5 tools):**

| Tool | Description |
|------|-------------|
| `search_assets(query, limit)` | Search assets by name or code |
| `get_asset_details(asset_id)` | Full asset detail including warranty and open WO count |
| `search_locations(query)` | List or search facility locations |
| `find_ppm_schedules(asset_id, overdue_only)` | Find PPM schedules, optionally overdue only |
| `get_dashboard_stats()` | Aggregate WO counts, backlog, and asset health summary |

---

### 4.3 Migration Agent — Data Import Lifecycle (6 tools)

**Purpose:** Manage the end-to-end import of customer CMMS data (CSV/Excel)
into the `plenum_cafm` schema via the AI schema mapper.

**Implementation:** HTTP calls to `svc-ai-schema-mapper` (port 8003) via shared
`http_client.request()`. `import_records` and `rollback_migration` use `max_attempts=1`
(no retry) to prevent duplicate writes and double-deletes.

**HITL gates:**
- **Gate 1 — map_fields:** Fires `interrupt()` when any field confidence < 0.85.
  Human can approve as-is, reject, or supply field corrections.
- **Gate 2 — rollback_migration:** Always fires `interrupt()` before the destructive delete.
  Requires explicit `{"confirmed": true}` to proceed.

**Intended sequence:**
```
parse_csv_file → map_fields [Gate 1 if low confidence]
    → validate_schema → import_records → get_migration_status (poll)
    → (on error) rollback_migration [Gate 2 always]
```

| Tool | Description |
|------|-------------|
| `parse_csv_file(file_path, encoding)` | Return headers + 50-row sample; default encoding latin1 |
| `map_fields(source_headers, cmms_source)` | AI-map headers → canonical CAFM fields with confidence scores |
| `validate_schema(mapping)` | Check mapping for type errors, missing required fields |
| `import_records(migration_id, approved)` | Trigger async import; approved=False for dry-run |
| `get_migration_status(migration_id)` | Poll status: pending/running/complete/failed |
| `rollback_migration(migration_id, reason)` | Delete all rows written by a migration run |

---

### 4.4 Doc RAG Agent — Document Knowledge Base (6 tools)

**Purpose:** Index, search, and query unstructured documents (PDFs, DOCX, SOPs, manuals).
Uses pgvector semantic search inside doc-rag — no separate vector DB required.

**Implementation:** HTTP calls to `doc-rag` (port 8004) via shared `http_client.request()`.

| Tool | Description |
|------|-------------|
| `index_document(file_path, document_type)` | Embed + store a document; returns document_id |
| `query_docs(query, top_k)` | Natural language Q&A grounded in indexed documents |
| `semantic_search(query, filter_type)` | Return raw matching chunks without synthesis |
| `extract_text(file_path)` | One-off text extraction without indexing |
| `get_document_metadata(document_id)` | Filename, type, page count, chunk count, indexed_at |
| `delete_document(document_id)` | Remove a document and all its chunks permanently |

---

### 4.5 Compliance Agent — Regulatory Status (2 tools)

**Purpose:** Evaluate asset and portfolio compliance against maintenance schedules,
inspection outcomes, and open high-priority work orders.

**Implementation:** Direct SQL via `AsyncSessionLocal` — queries `scheduled_pm`, `work_orders`,
and `inspections` tables directly for performance.

**Compliance logic:**
- `non_compliant` — any overdue PM, or open High-risk corrective action inspection
- `at_risk` — open Highest/High WOs but no overdue PM
- `compliant` — all checks pass

| Tool | Description |
|------|-------------|
| `check_requirements(asset_code, regulation)` | Per-asset compliance check with findings list |
| `generate_compliance_report(scope, date_from, date_to)` | Portfolio summary with per-asset scores |

---

## 5. Shared HTTP client — `src/http_client.py`

All agent tools that call downstream services use a single shared async HTTP client
instead of creating `httpx.AsyncClient` per-call. This provides consistent retry
behaviour and circuit breaking across all 5 HTTP-backed domains.

```python
from ..http_client import request as _request

resp = await _request(
    "POST", settings.wo_management_base_url, "/api/work-orders/",
    service="wo_management", timeout=45.0,
)
```

### Retry strategy (tenacity)

- Up to `max_attempts` attempts per call (default 3)
- Retries on: `httpx.TransportError` (connection/timeout) and 5xx responses
- Does **not** retry on 4xx — client errors won't resolve on retry
- Exponential backoff: 1s → 2s → 4s (capped at 8s)
- `import_records` and `rollback_migration` use `max_attempts=1` — never retry non-idempotent operations

### Circuit breaker (per service)

- Keyed by the `service` name string (e.g. `"wo_management"`, `"migration"`, `"doc_rag"`)
- Opens after 5 consecutive failures; all calls raise `RuntimeError` while open
- Half-open probe attempt after 30 seconds to test recovery
- Resets fully on a successful response

---

## 6. HITL — Human-in-the-Loop

HITL is enabled when `settings.hitl_enabled = True` AND a Postgres checkpointer is configured.
`interrupt()` is the LangGraph primitive that pauses the graph.

### Active gates

| Gate | Tool | Trigger | Interrupt payload type |
|------|------|---------|------------------------|
| Gate 1 | `map_fields` | Any field confidence < 0.85 | `mapping_approval` |
| Gate 2 | `rollback_migration` | Always before destructive delete | `rollback_confirmation` |

### Gate 1 — mapping_approval

Human receives a payload listing low-confidence fields. Can respond with:
- `{"approved": true}` — accept mapping as-is
- `{"approved": false}` — cancel migration
- `{"approved": true, "corrections": {"Source Col": "canonical_field"}}` — correct fields

### Gate 2 — rollback_confirmation

Human receives a warning about the destructive delete. Must respond with:
- `{"confirmed": true}` — proceed with rollback
- `{"confirmed": false}` — cancel rollback

### Checkpointer

`AsyncPostgresSaver` from `langgraph-checkpoint-postgres`. Serialises graph state to
Postgres after each step. Allows the session to pause (browser close), then resume
later via `POST /api/workflow/resume/{session_id}`.

The `get_thread_state()` method reads `aget_state()` from LangGraph and exposes the
pending interrupt payload to the `GET /api/workflow/status/{session_id}` endpoint.

---

## 7. The system prompt strategy

The system prompt (`src/agents/system_prompt.py`) defines:

1. **Role** — what the agent is and its core responsibility
2. **Meta-capabilities** — `write_todos`, `task`, `write_file`, `read_file`, `memory_set`, `memory_get`
3. **4 orchestration modes** — Direct / Planned+Subagents / Filesystem Offload / Parallel
4. **Agent registry** — every tool documented with args and when to use it
5. **Decision routing** — intent → tool sequence table
6. **Output format rules** — 10 formatting constraints
7. **Hard rules** — 6 inviolable constraints

**Decision routing examples:**

| User intent | Tool sequence |
|-------------|---------------|
| New maintenance request | `search_assets` → `create_intelligent_work_order` |
| WO status / backlog | `list_work_orders` → `get_work_order` |
| Asset health | `search_assets` → `get_asset_details` → `list_work_orders(asset=...)` |
| PPM schedule check | `find_ppm_schedules(overdue_only=True)` → `check_requirements` |
| Parts stock level | `query_table('spare_parts', filters)` |
| Document question | `query_docs` or `semantic_search` |
| CMMS data import | `parse_csv_file` → `map_fields` → `validate_schema` → `import_records` |
| Compliance audit | `generate_compliance_report` |
| Operations briefing | `get_dashboard_stats` → `list_work_orders(status=active)` |
| Multi-domain report | `write_todos` → parallel `task()` calls → synthesise |

`build_system_prompt(extra_context)` appends per-request runtime context
(e.g. the calling user's role, active asset scope) without modifying the base prompt.

---

## 8. Orchestrator internals

```python
# src/agents/orchestrator.py

class DeepAgentOrchestrator:
    def __init__(self, openai_api_key, model="gpt-4o-mini", checkpointer=None):
        self._llm = init_chat_model(f"openai:{model}", temperature=0)
        self._agent = create_react_agent(
            model=self._llm,
            tools=ALL_TOOLS,           # all 46 tools registered
            checkpointer=checkpointer, # AsyncPostgresSaver in HITL mode, None otherwise
        )
        init_meta_tools(openai_api_key, model)   # initialises _TaskRunner for task()

    async def run(self, user_message, session_id, extra_context) -> dict:
        # stateless — fresh thread_id every call
        set_session_context(thread_id)
        result = await self._agent.ainvoke(input_, config)
        return {session_id, answer, tool_calls, success, interrupted, interrupt_payload}

    async def run_stateful(self, user_message, session_id, extra_context) -> dict:
        # uses session_id as thread_id — state persisted to Postgres
        set_session_context(session_id)
        result = await self._agent.ainvoke(input_, config)
        # __interrupt__ in result → interrupted=True + interrupt_payload

    async def resume(self, session_id, decision) -> dict:
        # Command(resume=decision) feeds the human answer into the paused graph
        return await self._invoke(Command(resume=decision), session_id, session_id)

    async def stream(self, user_message, session_id, extra_context) -> AsyncGenerator[dict]:
        # astream_events(version="v2") → typed event dicts for WebSocket
        # catches GraphInterrupt → gate_interrupt event
        async for event in self._agent.astream_events(input_, config, version="v2"):
            if event["event"] == "on_tool_start": yield tool_started_event
            if event["event"] == "on_tool_end":   yield tool_completed_event
            if event["event"] == "on_chat_model_end" and not tool_calls: final_answer = ...
        yield {"type": "workflow_completed", "answer": final_answer}
```

**Key design decisions:**

- `temperature=0` — deterministic tool selection and reasoning
- Singleton instance — created once during FastAPI lifespan, shared across all requests
- `set_session_context(thread_id)` called before every `ainvoke` — namespaces meta-tool state
- `_TOOL_DOMAIN` dict maps all 38 tool names to their domain — drives `agent_switch` events
- Full tool trace returned on every REST call — every tool name, input, and output visible

---

## 9. Application startup sequence

```
uvicorn starts
    ↓
lifespan() enters
    ↓
_configure_logging()          ← structlog: ConsoleRenderer (debug) or JSONRenderer (prod)
    ↓
init_session_factory()        ← creates AsyncSessionLocal for UDR + Compliance agents
    ↓
checkpointer = AsyncPostgresSaver.from_conn_string(settings.db_url)
               if settings.hitl_enabled else None
    ↓
DeepAgentOrchestrator(
    openai_api_key, model, checkpointer
)                             ← validates OpenAI key, builds LangGraph agent, init_meta_tools()
    ↓
app.state.orchestrator = ... ← mounted for dependency injection
    ↓
FastAPI ready on port 8008
```

If `OPENAI_API_KEY` is missing or invalid the startup fails with a clear error.
`DB_URL` is validated on first DB tool call (lazy — UDR and Compliance agents use direct DB).

---

## 10. API surface

### Stateless request

```
POST /api/workflow/run
{ "message": "...", "session_id": null, "context": null }

→ 200
{ "session_id": "uuid", "answer": "...", "tool_calls": [...], "success": true,
  "interrupted": false, "interrupt_payload": null }
```

### HITL-capable request

```
POST /api/workflow/run-stateful
{ "message": "...", "session_id": "required-uuid", "context": null }

→ 200 (gate triggered)
{ "session_id": "required-uuid", "answer": "", "tool_calls": [...], "success": true,
  "interrupted": true,
  "interrupt_payload": {
    "type": "mapping_approval",
    "low_confidence_fields": [...],
    "instructions": "Respond with {approved, corrections}"
  }
}
```

### Submit human decision

```
POST /api/workflow/resume/{session_id}
{ "decision": {"approved": true, "corrections": {"WeirdCol": "asset_code"}} }

→ 200 (same WorkflowResponse shape — execution continues from interrupt point)
```

### Check interrupt status

```
GET /api/workflow/status/{session_id}

→ 200
{ "session_id": "uuid", "interrupted": true,
  "interrupt_payload": { "type": "rollback_confirmation", ... } }
```

### WebSocket streaming

```
WS /api/workflow/ws/{session_id}

Client sends:  {"message": "...", "context": "optional"}

Server streams:
  {"type": "tool_started",       "tool": "find_ppm_schedules", "domain": "wo_engine", "input": {...}}
  {"type": "tool_completed",     "tool": "find_ppm_schedules", "domain": "wo_engine", "output": [...]}
  {"type": "agent_switch",       "from_domain": "wo_engine",   "to_domain": "compliance"}
  {"type": "gate_interrupt",     "payload": {...},              "session_id": "..."}
  {"type": "workflow_completed", "answer": "...",               "session_id": "..."}
  {"type": "error",              "error":  "...",               "session_id": "..."}

Connection closes after workflow_completed, gate_interrupt, or error.
```

### Infrastructure

```
GET /health  → {"status": "ok", "service": "svc-deepagents", "version": "1.0.0"}
GET /docs    → Swagger UI
GET /redoc   → ReDoc
```

---

## 11. Configuration

All settings loaded from `.env` via pydantic-settings (`src/config.py`).

| Variable | Purpose | Default |
|----------|---------|---------|
| `OPENAI_API_KEY` | gpt-4o-mini API key | required |
| `OPENAI_MODEL` | Model to use | `gpt-4o-mini` |
| `ANTHROPIC_API_KEY` | Reserved for Claude subagent use | optional |
| `DB_URL` | Azure PostgreSQL connection (asyncpg) | required |
| `WO_MANAGEMENT_BASE_URL` | svc-work-order-management URL | `http://localhost:8007` |
| `DOC_RAG_BASE_URL` | doc-rag URL | `http://localhost:8004` |
| `MIGRATION_BASE_URL` | svc-ai-schema-mapper URL | `http://localhost:8003` |
| `HITL_ENABLED` | Enable HITL interrupt gates | `false` |
| `LANGSMITH_API_KEY` | LangSmith tracing key | optional |
| `LANGSMITH_PROJECT` | LangSmith project name | optional |
| `LANGSMITH_TRACING` | Enable LangSmith trace export | `false` |
| `PORT` | Port to listen on | `8008` |
| `DEBUG` | `true` = coloured logs + DEBUG level | `false` |

---

## 12. Tool count verification

| Group | Tools | Downstream |
|-------|-------|------------|
| Meta-capability tools | 6 | In-process (ContextVar + _TaskRunner) |
| UDR Agent | 2 | Azure PostgreSQL (direct asyncpg) |
| WO Engine Agent | 16 | svc-work-order-management :8007 |
| Migration Agent | 6 | svc-ai-schema-mapper :8003 |
| Doc RAG Agent | 6 | doc-rag :8004 |
| Compliance Agent | 2 | Azure PostgreSQL (direct asyncpg) |
| **Total** | **38** | |

**Confidence threshold:** `_CONFIDENCE_THRESHOLD = 0.85` in `migration_agent.py`.
The `ARCHITECTURE.md` previously stated 0.80 (matching CLAUDE.md EL-3.0); the code
uses 0.85. The code is authoritative. CLAUDE.md's EL-3.0 threshold (0.80) applies
to the schema mapper layer; the HITL gate in the orchestrator's `map_fields` tool
uses a stricter 0.85 to give the LLM headroom before interrupting the user.

---

## 13. Logging

Uses `structlog` in the same environment-aware pattern as `svc-udr`:

- `DEBUG=true` → coloured `ConsoleRenderer` for local development
- `DEBUG=false` → `JSONRenderer` for Azure Monitor / production

Key log events:

| Event | Level | When |
|-------|-------|------|
| `svc-deepagents.startup` | INFO | Application starting |
| `orchestrator.ready` | INFO | Agent fully initialised — model, tool_count, hitl flag |
| `orchestrator.run.start` | INFO | Per-request: message received |
| `orchestrator.invoke.done` | INFO | Per-request: tool_call_count, interrupted flag |
| `orchestrator.invoke.error` | ERROR | Unhandled exception in agent loop |
| `orchestrator.stream.start` | INFO | WebSocket stream begun |
| `orchestrator.stream.gate_interrupt` | INFO | Gate interrupt caught in stream |
| `orchestrator.stream.done` | INFO | Stream completed normally |
| `orchestrator.stream.error` | ERROR | Unhandled error in event stream |
| `orchestrator.resume` | INFO | HITL resume: session_id + decision_keys |
| `migration.map_fields.hitl_gate` | INFO | Gate 1 interrupt queued |
| `migration.map_fields.rejected_by_human` | INFO | Human rejected field mapping |
| `migration.map_fields.corrections_applied` | INFO | Human corrections applied to mapping |
| `migration.rollback.hitl_gate` | INFO | Gate 2 interrupt queued |
| `migration.rollback.cancelled_by_human` | INFO | Rollback cancelled by human |
| `circuit_breaker.opened` | WARNING | Service circuit opened after 5 failures |
| `circuit_breaker.half_open` | INFO | Half-open probe attempt |
| `circuit_breaker.closed` | INFO | Circuit recovered after successful probe |
| `http_client.retry` | INFO | Retry attempt: service, method, path, attempt# |
| `ws_workflow.start` | INFO | WebSocket connection accepted |
| `ws_workflow.error` | ERROR | Unhandled error in WebSocket handler |
| `udr.query_table.unsafe_table` | WARNING | SQL injection attempt blocked |
| `udr.query_table.unsafe_column` | WARNING | SQL injection attempt blocked |

---

## 14. Integration with the wider CAFM platform

```
cafm-connector-service :8000     ← CAFM data APIs (used by frontend directly)
svc-ingestion          :8001     ← Document/CSV ingestion pipelines
svc-query              :8002     ← Tier 1/2/3 structured query layer
svc-ai-schema-mapper   :8003     ← Migration tools call this
doc-rag                :8004     ← Doc RAG tools call this
table-editor           :8005     ← Standalone table editor (frontend direct)
svc-udr                :8006     ← Separate Anthropic-powered DB explorer
svc-work-order-mgmt    :8007     ← WO Engine tools call this
svc-deepagents         :8008     ← THIS SERVICE
```

`svc-deepagents` is the highest-level orchestrator. It does not replace `svc-udr` —
the UDR service has its own Anthropic-powered agent loop optimised for pure DB
exploration. The `lookup_user` and `query_table` tools here are lightweight
direct-SQL tools for cases where the orchestrator already knows what it needs.
