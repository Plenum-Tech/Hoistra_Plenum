# svc-deepagents — Frontend Integration & Example Flows

**Service:** `svc-deepagents` — DeepAgents Orchestration Layer  
**Port:** 8008  
**Date:** 2026-05-15

> **Current status:** The orchestrator UI is wired at `apps/frontend/src/features/ai/pipeline/deep-agent/`
> (`DeepAgentOrchestratorShell`, WebSocket streaming, HITL gates, **dynamic approval panel**).
> Requests go through `/backend/deep-agents` (or `DEEP_AGENTS_DEV_PROXY` in local dev).
> Work-order approval chains are executed via deep-agents tools → `svc-work-order-management` (:8007).

---

## 1. The Three Connection Patterns

The frontend has three ways to talk to svc-deepagents, depending on the use case.

---

### Pattern A — One-shot Chat (`POST /api/workflow/run`)

Use for any request where no human approval gate is needed. The entire agent run
completes synchronously before the response returns.

```
Frontend → POST http://localhost:8008/api/workflow/run
           {
             "message": "What are the 4 highest priority open work orders?",
             "session_id": "user-abc-123",       // optional, for grouping logs
             "context": "User role: FM Manager"  // optional, injected into system prompt
           }

           [Agent runs: GPT-4o reasons → calls tools → collects results]

Frontend ← {
             "session_id": "user-abc-123",
             "answer": "There are 4 Highest priority open WOs: ...",
             "tool_calls": [
               { "tool": "list_work_orders", "input": { "priority": "critical" }, "output": [...] }
             ],
             "success": true,
             "interrupted": false,
             "interrupt_payload": null
           }
```

**When to use:** Any standard chat interface, Q&A queries, dashboard questions, routine
work order operations, compliance checks, document searches.

**Rate limit:** 20 requests/minute per IP.

---

### Pattern B — HITL Stateful (`POST /api/workflow/run-stateful` → `POST /api/workflow/resume/{id}`)

Use when a long-running workflow needs to persist state across multiple turns — for example,
a multi-step operation where the user provides input mid-session and the agent needs to
remember the full prior context to continue.

> **Migration note:** The migration pipeline no longer uses LangGraph `interrupt()` gates.
> `run_migration` drives the pipeline automatically and returns gate payloads as structured
> JSON in the tool result. The orchestrator surfaces these to the user through the normal
> chat reply — no `run-stateful` or `resume` required. Migration works fully through Pattern A.

The Pattern B flow (for any tool that does use `interrupt()`):

```
Step 1 — Kick off the stateful workflow

Frontend → POST /api/workflow/run-stateful
           {
             "message": "...",
             "session_id": "session-xyz-789"   // REQUIRED — used as LangGraph thread ID
           }

           [Agent runs → a tool calls interrupt() → PAUSES]

Frontend ← {
             "session_id": "session-xyz-789",
             "answer": "",
             "tool_calls": [...],
             "success": true,
             "interrupted": true,
             "interrupt_payload": {
               "type": "...",
               "message": "..."
             }
           }

Step 2 — UI surfaces the interrupt payload to the user for their decision.

Step 3 — Submit the human decision

Frontend → POST /api/workflow/resume/session-xyz-789
           {
             "decision": { ... }    // format depends on the interrupt type
           }

           [Agent resumes from where it paused]

Frontend ← {
             "session_id": "session-xyz-789",
             "answer": "...",
             "interrupted": false,
             "success": true
           }
```

**Checking status between steps** (optional polling):

```
Frontend → GET /api/workflow/status/session-xyz-789
Frontend ← {
             "session_id": "session-xyz-789",
             "interrupted": true,
             "interrupt_payload": { ... }
           }
```

---

### Pattern C — WebSocket Streaming (`WS /api/workflow/ws/{session_id}`)

Use for a "watching the agent think" UX — tool calls stream to the frontend in real time
as they happen, so the user sees progress instead of a blank spinner.

```
Step 1 — Connect
Frontend → WebSocket connect to ws://localhost:8008/api/workflow/ws/session-abc

Step 2 — Send the request (one message after connect)
Frontend → { "message": "Run the morning operations briefing", "context": null }

Step 3 — Receive event stream (one JSON object per event)
Frontend ←  { "type": "tool_started",   "tool": "list_work_orders",          "domain": "wo_engine",   "input": { "priority": "critical" } }
Frontend ←  { "type": "tool_completed", "tool": "list_work_orders",          "domain": "wo_engine",   "output": [ ... ] }
Frontend ←  { "type": "agent_switch",   "from_domain": "wo_engine",          "to_domain": "udr" }
Frontend ←  { "type": "tool_started",   "tool": "query_table",               "domain": "udr",         "input": { "table_name": "spare_parts", "filters": { "stock_on_hand": 0 } } }
Frontend ←  { "type": "tool_completed", "tool": "query_table",               "domain": "udr",         "output": [ ... ] }
Frontend ←  { "type": "agent_switch",   "from_domain": "udr",                "to_domain": "compliance" }
Frontend ←  { "type": "tool_started",   "tool": "generate_compliance_report","domain": "compliance",  "input": { "scope": "all_assets" } }
Frontend ←  { "type": "tool_completed", "tool": "generate_compliance_report","domain": "compliance",  "output": { ... } }
Frontend ←  { "type": "workflow_completed", "answer": "Morning briefing:\n- 4 critical WOs...\n- 0 parts at zero stock\n- Compliance rate: 82%", "session_id": "session-abc" }

Step 4 — Server closes the WebSocket after workflow_completed, gate_interrupt, or error.
```

**All event types:**

| Event type | When it fires | Key fields |
|-----------|--------------|-----------|
| `tool_started` | A tool call begins | `tool`, `domain`, `input` |
| `tool_completed` | A tool call finishes | `tool`, `domain`, `output` |
| `agent_switch` | Active domain changes between consecutive tools | `from_domain`, `to_domain` |
| `gate_interrupt` | A HITL `interrupt()` fired inside a tool | `payload`, `session_id` |
| `workflow_completed` | Agent returned its final answer | `answer`, `session_id` |
| `error` | Unexpected exception | `error`, `session_id` |

> **Note:** `gate_interrupt` events from the WebSocket stream cannot be resumed via the same
> WebSocket connection. To resume, use `POST /api/workflow/resume/{session_id}` over HTTP.
> For full HITL support, prefer Pattern B (`run-stateful`) instead of WebSocket.

---

## 2. Discovering Available Tools

```
Frontend → GET http://localhost:8008/api/workflow/tools

Frontend ← [
  { "name": "write_todos",                   "description": "...", "domain": "meta" },
  { "name": "task",                          "description": "...", "domain": "meta" },
  { "name": "lookup_user",                   "description": "...", "domain": "udr" },
  { "name": "query_table",                   "description": "...", "domain": "udr" },
  { "name": "create_intelligent_work_order", "description": "...", "domain": "wo_engine" },
  { "name": "suggest_approval_chain",        "description": "...", "domain": "wo_engine" },
  ...  // all 46 tools
]
```

---

## 2.1 Dynamic approval UI (orchestrator chat)

When the agent calls `suggest_approval_chain`, `request_approval_chain`, or `get_approval_chain`,
the frontend parses `tool_completed` / REST `tool_calls` output and renders
`DeepAgentApprovalPanel` (`approval-suggestion-parse.ts`).

**Parsed fields:**

| Source field | UI |
|--------------|-----|
| `auto_suggestion.message` | Agent summary block |
| `auto_suggestion.recommended_chain_summary` | Recommended path headline |
| `chain` / `recommended_steps` | Ordered approver list |
| `previous_approval_processes[]` | Up to 3 historical cards (WO id, match %, chain, hours) |
| `confidence`, `match_score`, `risk_score` | Badges in panel header |
| `get_approval_chain` → `chain[]` with `status` | Per-step status chips (pending / approved / rejected) |

**Hook:** `useDeepAgentOrchestrator` exposes `approvalInsight` (latest matching tool output).
**Shell:** Panel appears in the main chat column and in the right-rail **Tools** tab.

**Starter prompt (built-in):** *"Who should approve an urgent HVAC repair at Building A Roof? Show similar past approvals."*

**Demo data (prod/local DB):** From `svc-work-order-management`, run
`python -m scripts.seed_approval_demo` (or set `SEED_APPROVAL_DEMO=true` on `backend-app` in
`docker-compose.single-url.local.yml` to seed on container start). Creates approver users,
`WO-DEMO-APPR-HVAC-*` historical WOs, and multi-step approved chains for `suggest_approval_chain`.

---

## 3. Example Flows — What the Orchestrator Actually Does Internally

The orchestrator is a GPT-4o ReAct agent. For each request it:
1. Reads the system prompt (46-tool registry + 4-mode strategy)
2. Reasons about which mode and tools to use
3. Calls tools in sequence (or spawns parallel subagents)
4. Returns a grounded answer

The examples below show the complete internal trace for each scenario.

---

### Flow 1 — Simple lookup (Mode 1: Direct)

**Request:** `"What is the status of work order WO-20240115143022123456?"`

```
Orchestrator reasoning:
  → Single WO ID provided → get_work_order is the exact tool
  → One tool, one domain → Mode 1 (no write_todos, no task())

Tool calls:
  1. get_work_order("WO-20240115143022123456")
       HTTP GET http://localhost:8007/api/work-orders/WO-20240115143022123456
       ← {
            "id": "WO-20240115143022123456",
            "status": "preparing",
            "priority": "high",
            "asset": "MOB-AHU-001",
            "location": "Level 2 Plant Room",
            "issue_description": "Cooling coil blockage detected",
            "vendor": "CoolTech Services",
            "scheduled_date": "2026-05-17",
            "created_at": "2026-05-14T09:22:00Z"
          }

Answer returned to frontend:
  "Work order WO-20240115143022123456 is currently in Preparing status.
   Asset: MOB-AHU-001 (Level 2 Plant Room). Priority: High.
   Issue: Cooling coil blockage detected. Assigned vendor: CoolTech Services.
   Scheduled for 2026-05-17. Created 1 day ago."
```

---

### Flow 2 — Dynamic approval preview (Mode 1: Direct, WO Engine)

**Request:** `"Who should approve an urgent HVAC repair at Building A Roof?"`

```
Tool calls:
  1. suggest_approval_chain(
       work_type="repair", priority="urgent", location="Building A Roof", asset_category="HVAC")
       POST http://localhost:8007/api/work-orders/suggest-approval
       ← { auto_suggestion: { message: "...", recommended_chain_summary: "..." },
            chain: [...], previous_approval_processes: [...], confidence: "high", ... }

Frontend:
  - tool_completed event → parseApprovalToolOutput → approvalInsight state
  - DeepAgentApprovalPanel shows chain + past processes + full message

Answer:
  Presents auto_suggestion.message in chat; user confirms approvers before WO creation.
```

**Create WO after confirm:**

```
  2. create_intelligent_work_order(...)
  3. request_approval_chain(work_order_id, approval_type="preparation")
```

---

### Flow 3 — Zero-stock parts check (Mode 1: Direct, UDR domain)

**Request:** `"Which spare parts are at zero stock?"`

```
Orchestrator reasoning:
  → Structured data query, spare_parts table → query_table tool via UDR agent
  → One tool, one domain → Mode 1

Tool calls:
  1. query_table("spare_parts", {"stock_on_hand": 0})
       SQL: SELECT * FROM plenum_cafm.spare_parts WHERE stock_on_hand = :p_stock_on_hand LIMIT 100
       ← [
            { "part_code": "MOTOR-8HP", "part_name": "8HP Motor", "stock_on_hand": 0,
              "minimum_allowed_stock": 2, "supplier": "ElectroParts UAE" },
            ...
          ]

Answer:
  "1 spare part is at zero stock — this is CRITICAL:

  | Part Code  | Part Name | Min Stock | Supplier         |
  |------------|-----------|-----------|------------------|
  | MOTOR-8HP  | 8HP Motor | 2         | ElectroParts UAE |

  Immediate reorder recommended."
```

---

### Flow 3 — Asset compliance check (Mode 1: Direct, Compliance domain)

**Request:** `"Is MOB-AHU-001 compliant?"`

```
Orchestrator reasoning:
  → Compliance check on a specific asset → check_requirements tool
  → One tool, one domain → Mode 1

Tool calls:
  1. check_requirements("MOB-AHU-001", regulation=None)
       SQL queries directly on DB:
         - scheduled_pm WHERE asset_code = 'MOB-AHU-001' → checks is_overdue
         - work_orders WHERE asset_code = 'MOB-AHU-001' AND priority IN ('Highest','High') AND status != 'Closed'
         - inspections WHERE asset_code = 'MOB-AHU-001' AND corrective_action = true
       ← {
            "asset_code": "MOB-AHU-001",
            "compliance_status": "non_compliant",
            "findings_count": 2,
            "findings": [
              { "type": "pm_overdue", "sm_code": "SM-AHU-001-Q", "last_pm_date": "2026-01-10" },
              { "type": "high_priority_open_wo", "wo_code": "WO-20240115143022123456", "priority": "High" }
            ]
          }

Answer:
  "MOB-AHU-001 is NON-COMPLIANT. 2 findings:
  1. PM overdue — Schedule SM-AHU-001-Q last completed 2026-01-10 (125 days ago)
  2. Open High-priority work order WO-20240115143022123456 still active

  Recommended action: Schedule the overdue PM immediately and close or escalate the open WO."
```

---

### Flow 4 — Create a work order via intelligent pipeline (Mode 1: WO Engine)

**Request:** `"Raise a work order for MOB-AHU-001 — the cooling coil is blocked. Priority high, requester is Ahmed Al-Rashid, ahmed@facility.ae"`

```
Orchestrator reasoning:
  → New WO request with all details → create_intelligent_work_order (not create_work_order)
  → One tool, one domain → Mode 1

Tool calls:
  1. create_intelligent_work_order(
       source="manual",
       asset="MOB-AHU-001",
       location="Level 2 Plant Room",
       issue_description="Cooling coil blocked",
       priority="high",
       request_type="repair",
       requester_name="Ahmed Al-Rashid",
       requester_email="ahmed@facility.ae"
     )
       HTTP POST http://localhost:8007/api/chat/
       Body: { "message": "Please create a work order with the following details:\nSource: manual\nAsset: MOB-AHU-001\n..." }

       [svc-work-order-management runs its own 15-step AI pipeline internally:
        criticality scoring → safety analysis → compliance check → asset intelligence →
        vendor scoring → resource allocation → smart scheduling → workspace pinning →
        journey log creation]

       ← {
            "work_order_id": "WO-20260515162201987654",
            "status": "pending_approval",
            "priority": "high",
            "vendor": "CoolTech Services",
            "scheduled_date": "2026-05-17",
            "estimated_duration": 3.5,
            "agent_reply": "Work order created. CoolTech Services assigned based on vendor scoring. Scheduled 2026-05-17."
          }

Answer:
  "Work order WO-20260515162201987654 created for MOB-AHU-001.
   Status: Pending Approval. Vendor: CoolTech Services (auto-assigned).
   Scheduled: 2026-05-17, estimated 3.5 hours.
   Use approve_work_order to move it to Preparing."
```

---

### Flow 5 — Morning operations briefing (Mode 4: Parallel spawning)

**Request:** `"Give me the morning briefing — critical WOs, today's PPM tasks, zero-stock parts, and overall compliance rate."`

```
Orchestrator reasoning:
  → 4 independent data fetches across 3 domains
  → None depend on each other → Mode 4 (parallel)
  → write_todos() first, then fire all task() calls in the same turn

Internal agent actions:

  write_todos([
    "Step 1 (parallel): Get critical/urgent open WOs | Get today's PPM schedules | Get zero-stock parts | Get compliance summary",
    "Step 2: Synthesise into morning briefing"
  ])

  [All four task() calls fire concurrently — GPT-4o issues them in one turn]

  task("wo_engine", "List all open work orders with priority urgent or critical. Return work_order_id, asset, priority, issue_description, created_at.")
     → list_work_orders(status=None, priority="critical")
     → list_work_orders(status=None, priority="urgent")
     ← [ { "work_order_id": "WO-...", "asset": "MOB-AHU-001", "priority": "critical", ... }, ... ]

  task("wo_engine", "Get all PPM schedules due today 2026-05-15 or overdue. Return schedule_id, asset_id, description, next_due_date.")
     → find_ppm_schedules(overdue_only=True)
     ← [ { "schedule_id": "...", "asset_id": "MOB-GEN-001", "description": "Generator 1000hr service", "next_due_date": "2026-04-01" } ]

  task("udr", "Query the spare_parts table where stock_on_hand = 0. Return part_code, part_name, supplier, minimum_allowed_stock.")
     → query_table("spare_parts", {"stock_on_hand": 0})
     ← [ { "part_code": "MOTOR-8HP", "part_name": "8HP Motor", "supplier": "ElectroParts UAE", "minimum_allowed_stock": 2 } ]

  task("compliance", "Generate a compliance report for scope=all_assets. Return only the summary block.")
     → generate_compliance_report(scope="all_assets")
     ← { "summary": { "total_assets": 60, "compliant": 49, "at_risk": 7, "non_compliant": 4, "compliance_rate_pct": 81.7 } }

  [All four results collected → synthesise]

Answer:
  "## Morning Operations Briefing — 2026-05-15

  ### Critical Work Orders (2)
  | WO ID | Asset | Priority | Age | Issue |
  |-------|-------|----------|-----|-------|
  | WO-2026... | MOB-AHU-001 | Critical | 1d | Cooling coil blockage |
  | WO-2026... | MOB-CHI-002 | Urgent | 3d | Chiller compressor fault |

  ### Overdue PPM Tasks (1)
  - MOB-GEN-001: Generator 1000hr service (due 2026-04-01 — 44 days overdue)

  ### Parts at Zero Stock (1 — CRITICAL)
  - MOTOR-8HP: 8HP Motor — ElectroParts UAE (min stock: 2)

  ### Portfolio Compliance
  - 81.7% compliant (49/60 assets). 4 non-compliant, 7 at risk."
```

---

### Flow 6 — At-risk asset ranking (Mode 2: Planned + Subagents, cross-domain)

**Request:** `"Which assets are most at risk right now? Consider PM status, open WOs, and compliance."`

```
Orchestrator reasoning:
  → Requires data from UDR + WO Engine + Compliance → 3 domains
  → Results must be joined by asset_code → Mode 2
  → write_todos() first

Internal agent actions:

  write_todos([
    "Step 1: Get all assets from UDR Agent",
    "Step 2: Get all overdue PPM schedules from WO Engine Agent",
    "Step 3: Get all Highest/High priority open WOs from WO Engine Agent",
    "Step 4: Get portfolio compliance report from Compliance Agent",
    "Step 5: Join all datasets by asset_code, compute risk score, rank top 10"
  ])

  [Steps 1-4 can run in parallel — fire all task() calls together]

  task("udr", "Query the assets table. Return asset_code, asset_name, category, location_code for all assets.")
     → query_table("assets")
     ← [ { asset_code, asset_name, category, location_code } × 60 ]

  task("wo_engine", "Get all PPM schedules where overdue_only=true.")
     → find_ppm_schedules(overdue_only=True)
     ← [ { schedule_id, asset_id, next_due_date } × N ]

  task("wo_engine", "List all open work orders with priority urgent or critical or high. Return work_order_id, asset, priority, created_at.")
     → list_work_orders(priority="critical") + list_work_orders(priority="high")
     ← [ ... WOs ... ]

  task("compliance", "Generate compliance report for scope=all_assets. Return asset_code and compliance_status for every asset.")
     → generate_compliance_report(scope="all_assets")
     ← { asset_details: [ { asset_code, compliance_status, overdue_pms, open_high_priority_wos } ] }

  [Orchestrator joins by asset_code, computes risk score:
    - overdue PM → +3 points
    - open critical WO → +3 points each
    - open high WO → +2 points each
    - non_compliant → +2 points
    - at_risk → +1 point
   Ranks descending]

Answer:
  "Top 5 At-Risk Assets:

  1. MOB-AHU-001 — Air Handler (Level 2) — Score 8
     ↳ Overdue PM + 1 High WO + Non-compliant
  2. MOB-GEN-001 — Generator (Plant Room) — Score 6
     ↳ Overdue 1000hr PM + At-risk compliance
  3. MOB-CHI-002 — Chiller (Roof) — Score 5
     ↳ 1 Urgent WO + At-risk compliance
  ...

  Recommended: Prioritise MOB-AHU-001 immediately — overdue PM + active fault + non-compliant."
```

---

### Flow 7 — Document Q&A (Mode 1: Doc RAG domain)

**Request:** `"What does the AHU manual say about belt tension specifications?"`

```
Orchestrator reasoning:
  → Question about a specific document → Doc RAG domain
  → One tool call → Mode 1

Tool calls:
  1. query_docs("What are the belt tension specifications for AHU?", top_k=5)
       HTTP POST http://localhost:8004/api/query
       [doc-rag service retrieves top-5 chunks from pgvector, synthesises answer]
       ← {
            "answer": "Belt tension should be checked with a tension meter. Correct deflection is 10-15mm at mid-span under 2kg force...",
            "sources": [ { "document": "AHU_Maintenance_Manual_v3.pdf", "page": 47, "chunk": "..." } ]
          }

Answer:
  "According to AHU_Maintenance_Manual_v3.pdf (page 47):
   Belt tension deflection should be 10-15mm at mid-span under 2kg applied force.
   Check monthly and re-tension if deflection exceeds 20mm."
```

---

### Flow 8 — Email work order intake (Mode 1: WO Engine)

**Request:** `"Process this email from a tenant: Subject: AC not working in Suite 304, Body: The air conditioning in Suite 304 has been making loud noises and blowing warm air since yesterday morning. Please send someone urgently. From: Sarah Johnson, sarah@tenant.ae"`

```
Orchestrator reasoning:
  → Email-to-WO conversion → process_email_work_order tool
  → One tool, one domain → Mode 1

Tool calls:
  1. process_email_work_order(
       subject="AC not working in Suite 304",
       body="The air conditioning in Suite 304 has been making loud noises and blowing warm air since yesterday morning. Please send someone urgently.",
       sender_name="Sarah Johnson",
       sender_email="sarah@tenant.ae"
     )
       HTTP POST http://localhost:8007/api/chat/email
       [svc-work-order-management: extracts asset, location, issue → creates WO with AI assessment]
       ← {
            "work_order_id": "WO-20260515171100123",
            "status": "pending_approval",
            "asset": "AHU-Suite-304",
            "priority": "urgent",
            "agent_reply": "WO created. AC unit for Suite 304 identified. Priority set to Urgent based on tenant impact and noise/warm air symptoms. Vendor AlCool notified."
          }

Answer:
  "Work order WO-20260515171100123 created from Sarah Johnson's email.
   Asset: AHU-Suite-304. Priority: Urgent (elevated from email urgency + symptoms).
   Status: Pending Approval. Vendor AlCool assigned and notified."
```

---

### Flow 9 — Schema mapper migration with HITL gates (Mode 2: Planned + conversational)

**Request:** `"Import the Maximo asset export at /uploads/maximo_assets.csv"`

The orchestrator drives the entire pipeline via `run_migration`. No LangGraph interrupt() —
gate payloads are returned as structured JSON and surfaced to the user through normal chat.
This works through Pattern A (`POST /run`) — no stateful session required.

```
Orchestrator reasoning:
  → Migration request → Migration domain tools
  → 9-node async pipeline in svc-ai-schema-mapper (port 8003)
  → start_migration → run_migration (auto-drives pipeline, surfaces gates conversationally)
  → Mode 2 (sequential: start → run → [user decision → submit → run] × N → complete)

  write_todos([
    "Step 1: Upload file with start_migration, get migration_id",
    "Step 2: Call run_migration — it auto-advances step_paused nodes and surfaces gates",
    "Step 3: Show Gate 0 (pre_semantic) payload to user, collect decision, call submit_pre_semantic",
    "Step 4: Call run_migration again — show Gate 1 (field_mapping) payload, collect decisions",
    "Step 5: Call submit_field_mapping with user corrections, then run_migration again",
    "Step 6: Show Gate 2 (hierarchy) payload to user, call submit_hierarchy",
    "Step 7: run_migration auto-confirms the write gate and returns complete"
  ])

  1. start_migration(
       file_path="/uploads/maximo_assets.csv",
       cmms_name="Maximo",
       organization_id="00000000-0000-0000-0000-000000000001"
     )
       HTTP POST http://localhost:8003/api/migration/start-with-upload (multipart)
       ← { "migration_id": "mig-abc-def-123", "status": "running" }

  2. run_migration("mig-abc-def-123")
       [Internally polls in 2s loop:]
         GET /api/migration/mig-abc-def-123/status → step_paused (ingest_node)
           → POST /api/migration/mig-abc-def-123/advance  (auto-advance)
         GET /api/migration/mig-abc-def-123/status → running
         GET /api/migration/mig-abc-def-123/status → step_paused (deterministic_mapper_node)
           → POST /api/migration/mig-abc-def-123/advance  (auto-advance)
         GET /api/migration/mig-abc-def-123/status → awaiting_review (pre_semantic)
       ← {
            "status": "gate",
            "gate_type": "pre_semantic",
            "migration_id": "mig-abc-def-123",
            "payload": {
              "t1_mapped_by_table": {
                "assets": [
                  { "source_field": "ASSET_NUM",   "canonical_field": "asset_code",    "confidence": 0.99 },
                  { "source_field": "DESCRIPTION",  "canonical_field": "asset_name",    "confidence": 0.97 },
                  { "source_field": "ASSET_TYPE",   "canonical_field": "category",      "confidence": 0.91 },
                  { "source_field": "LOCATION",     "canonical_field": "location_code", "confidence": 0.94 },
                  { "source_field": "SERIAL_NUM",   "canonical_field": "serial",        "confidence": 0.96 }
                ]
              },
              "unresolved_by_table": {
                "assets": [
                  { "source_field": "EQUIP_CAT",  "reason": "below_t1_threshold" },
                  { "source_field": "MAINT_TYPE",  "reason": "below_t1_threshold" }
                ]
              }
            },
            "message": "Gate 0 (Pre-Semantic): Review T1 mappings..."
          }

  [Orchestrator shows Gate 0 payload to user in chat reply]

  Chat response to user:
    "The pipeline has paused at Gate 0 (Pre-Semantic review). Here are the T1 mappings:
     - ASSET_NUM → asset_code (0.99) ✓
     - DESCRIPTION → asset_name (0.97) ✓
     - ASSET_TYPE → category (0.91) ✓
     - LOCATION → location_code (0.94) ✓
     - SERIAL_NUM → serial (0.96) ✓
     EQUIP_CAT and MAINT_TYPE will go to semantic phase.
     Reply 'approve all' to continue, or tell me any corrections."

  [User replies: "looks good, approve all"]

  3. submit_pre_semantic("mig-abc-def-123", approve_all=True)
       HTTP POST http://localhost:8003/api/migration/mig-abc-def-123/gate/pre-semantic
       ← { "status": "running", "message": "Proceeding to semantic mapper" }

  4. run_migration("mig-abc-def-123")
       [Polls: semantic_mapper runs → EL-3.0: 2 fields below 0.80 → field_mapping gate]
       ← {
            "status": "gate",
            "gate_type": "field_mapping",
            "migration_id": "mig-abc-def-123",
            "payload": {
              "tier2_flagged_by_table": {
                "assets": [
                  { "source_field": "MAINT_TYPE", "canonical_field": "maintenance_type", "confidence": 0.68 },
                  { "source_field": "EQUIP_CAT",  "canonical_field": "category",         "confidence": 0.72 }
                ]
              }
            },
            "message": "Gate 1 (Field Mapping): Review low-confidence mappings..."
          }

  Chat response to user:
    "Gate 1 (Field Mapping): 2 fields have low confidence:
     - MAINT_TYPE → maintenance_type (0.68) — does this look right?
     - EQUIP_CAT → category (0.72) — confirm or override?
     Reply with your decisions or 'approve all'."

  [User replies: "MAINT_TYPE should map to wo_type, EQUIP_CAT is fine"]

  5. submit_field_mapping(
       migration_id="mig-abc-def-123",
       approve_all=False,
       flagged_decisions={
         "assets": [
           {"action": "override", "source_field": "MAINT_TYPE", "target_field": "wo_type", "rationale": "user correction"},
           {"action": "accept",   "source_field": "EQUIP_CAT"}
         ]
       }
     )
       HTTP POST http://localhost:8003/api/migration/mig-abc-def-123/gate/field-mapping
       ← { "status": "running", "message": "Field mapping gate passed" }

  6. run_migration("mig-abc-def-123")
       [Polls: preprocess → step_paused → auto-advance]
       [hierarchy_node runs → step_paused → auto-advance]
       [verify_hierarchy_node: awaiting_review (hierarchy gate)]
       ← {
            "status": "gate",
            "gate_type": "hierarchy",
            "migration_id": "mig-abc-def-123",
            "payload": {
              "detected_hierarchies": [
                { "parent": "locations", "child": "assets", "fk": "location_code", "confidence": 0.96 }
              ]
            },
            "message": "Gate 2 (Hierarchy): Review detected FK relationships..."
          }

  [User replies: "confirmed"]

  7. submit_hierarchy("mig-abc-def-123", approve_all=True)
       HTTP POST http://localhost:8003/api/migration/mig-abc-def-123/gate/hierarchy
       ← { "status": "running", "message": "Hierarchy gate passed" }

  8. run_migration("mig-abc-def-123")
       [Polls: output_generator runs → step_paused → auto-advance]
       [write_node: awaiting_review (write gate) → AUTO-CONFIRMED by run_migration]
         POST /api/migration/mig-abc-def-123/gate/final {"confirmed": true}
       [Pipeline runs to completion]
       ← { "status": "complete", "migration_id": "mig-abc-def-123" }

Answer:
  "Migration mig-abc-def-123 complete. 847 asset rows processed and handed off to svc-ingestion.
   Corrections applied: MAINT_TYPE → wo_type (your override). EQUIP_CAT → category confirmed.
   Mapping coverage: 98%. Use get_migration_mappings('mig-abc-def-123') for the full audit trail."
```

**Key difference from old flow:** `run_migration` replaces the old manual poll/advance loop.
All step pauses and the write gate are handled automatically. The frontend receives gate
payloads as part of the normal chat answer — no `interrupted=true` or `/resume` call needed.

---

### Flow 10 — Abandoning a migration before it completes

**Request:** `"Cancel migration mig-abc-def-123 — the file had wrong asset codes."`

```
Orchestrator reasoning:
  → User wants to abandon a migration mid-flight
  → The schema mapper pipeline writes NOTHING until the write gate (Gate 3) passes
  → run_migration auto-confirms the write gate only after all data gates are approved
  → If the user says to cancel before the write gate: simply stop calling run_migration
  → No rollback or cleanup needed — the pipeline state is abandoned in place

  [Option A: Pipeline is paused at a gate and user wants to stop]
  → Do nothing. Just inform the user the migration was not continued.
  → No data has been written to svc-ingestion. The migration_id is abandoned.

  [Option B: Need to check current state before deciding]

Tool calls:
  1. get_migration_status("mig-abc-def-123")
       ← {
            "status": "awaiting_review",
            "progress_pct": 50,
            "pending_gate_type": "field_mapping",
            "pending_gate_payload": { ... }
          }

  [Pipeline is at Gate 1 — nothing written yet]

Answer:
  "Migration mig-abc-def-123 has been stopped at Gate 1 (Field Mapping review, 50% progress).
   No data has been written to svc-ingestion — the pipeline only writes after Gate 3 is confirmed.
   To fix the file and retry, start a new migration with the corrected file:
   start_migration('/uploads/maximo_assets_fixed.csv', cmms_name='Maximo')"
```

> **Note:** The schema mapper pipeline writes nothing until the write gate passes.
> run_migration auto-confirms the write gate, so the only way to prevent a write is
> to not call run_migration after the hierarchy gate — or to check what gate the pipeline
> is at using get_migration_status and inform the user to stop before submitting hierarchy.
> There is no cancel endpoint. Simply abandoning the pipeline is the safe abort path.

---

### Flow 11 — Large asset health report with filesystem offload (Mode 3)

**Request:** `"Give me a full asset health report — every asset, its open WOs, PM status, and compliance."`

```
Orchestrator reasoning:
  → Full portfolio across all domains → 60 assets with cross-joined data → large result set
  → Mode 3 (filesystem offload to avoid context overflow) + Mode 4 (parallel fetches)

  write_todos([
    "Step 1 (parallel): Fetch all assets | Fetch all open WOs | Fetch all PPM schedules | Run compliance report",
    "Step 2: Write all datasets to temp files",
    "Step 3: Read and join by asset_code",
    "Step 4: Generate structured report"
  ])

  [Fire all 4 in parallel]
  task("udr",        "Query assets table. Return all rows.")                    → query_table("assets")
  task("wo_engine",  "List all open work orders all statuses. All fields.")    → list_work_orders(limit=200)
  task("wo_engine",  "Get all PPM schedules. Return all fields.")              → find_ppm_schedules()
  task("compliance", "Generate compliance report scope=all_assets.")           → generate_compliance_report()

  [Results arrive — each is large]

  write_file("report/assets.json",     json_dump(assets_result))      → "Written 18432 bytes to 'report/assets.json'"
  write_file("report/wos.json",        json_dump(wo_result))          → "Written 42100 bytes to 'report/wos.json'"
  write_file("report/ppm.json",        json_dump(ppm_result))         → "Written 3200 bytes to 'report/ppm.json'"
  write_file("report/compliance.json", json_dump(comp_result))        → "Written 9800 bytes to 'report/compliance.json'"

  [Read back only what's needed for the final join — no raw dump in context]
  assets     = read_file("report/assets.json")
  compliance = read_file("report/compliance.json")

  [Join and rank. Inline summarise WOs and PPM per asset.]

Answer:
  "## Asset Health Report — 2026-05-15 (60 assets)

  ### Portfolio Summary
  - Compliant: 49 (81.7%)  At-risk: 7  Non-compliant: 4

  ### Top 10 At-Risk Assets
  | Asset | Category | Status | Open WOs | Overdue PMs |
  |-------|----------|--------|----------|-------------|
  | MOB-AHU-001 | Air Handler | Non-compliant | 2 (1 High) | 1 |
  ...

  Full data written to session temp storage. Ask for specific asset details if needed."
```

---

### Flow 12 — Subagent spawning for parallel domain work (Mode 4, task() meta tool)

**Request:** `"Compare maintenance health of our Air Handlers vs Boilers."`

```
Orchestrator reasoning:
  → Two independent compliance + WO queries, different asset categories
  → Mode 4: fire 4 task() calls in parallel (2 domains × 2 categories)

  write_todos([
    "Step 1 (parallel): Compliance report Air Handler | Compliance report Boiler | Open WOs Air Handler | Open WOs Boiler",
    "Step 2: Compare side by side"
  ])

  task("compliance", "Generate compliance report for scope='Air Handler'. Return summary and asset_details.")
  task("compliance", "Generate compliance report for scope='Boiler'. Return summary and asset_details.")
  task("wo_engine",  "List all open work orders. I only need WOs where the asset category is Air Handler. Return work_order_id, asset, priority.")
  task("wo_engine",  "List all open work orders. I only need WOs where the asset category is Boiler. Return work_order_id, asset, priority.")

  [Each task() spawns an isolated sub-ReAct agent with only that domain's tools.
   Sub-agents run without checkpointer → no HITL inside task().]

  [All 4 complete → compare]

Answer:
  "## Air Handlers vs Boilers — Maintenance Health Comparison

  |                    | Air Handlers (22 assets) | Boilers (8 assets) |
  |--------------------|--------------------------|-------------------|
  | Compliant          | 16 (72.7%)               | 7 (87.5%)         |
  | At-risk            | 4                        | 1                 |
  | Non-compliant      | 2                        | 0                 |
  | Open WOs           | 9 (2 critical)           | 3 (0 critical)    |
  | Overdue PMs        | 3                        | 0                 |

  **Verdict:** Air Handlers are significantly more at risk. 2 non-compliant units and
  2 critical WOs warrant immediate attention. Boilers are in good health."
```

---

## 4. Configuration the Frontend Needs

```typescript
// Environment variables for the frontend
const DEEPAGENTS_BASE_URL = process.env.NEXT_PUBLIC_DEEPAGENTS_URL ?? "http://localhost:8008"
const DEEPAGENTS_WS_URL   = process.env.NEXT_PUBLIC_DEEPAGENTS_WS_URL ?? "ws://localhost:8008"

// The three endpoint roots
const endpoints = {
  run:        `${DEEPAGENTS_BASE_URL}/api/workflow/run`,
  runStateful:`${DEEPAGENTS_BASE_URL}/api/workflow/run-stateful`,
  resume:     (sid: string) => `${DEEPAGENTS_BASE_URL}/api/workflow/resume/${sid}`,
  status:     (sid: string) => `${DEEPAGENTS_BASE_URL}/api/workflow/status/${sid}`,
  tools:      `${DEEPAGENTS_BASE_URL}/api/workflow/tools`,
  ws:         (sid: string) => `${DEEPAGENTS_WS_URL}/api/workflow/ws/${sid}`,
  // Migration convenience endpoints (no LLM involved — wraps svc-ai-schema-mapper)
  migration: {
    start:            `${DEEPAGENTS_BASE_URL}/api/migration/start`,
    status:           (mid: string) => `${DEEPAGENTS_BASE_URL}/api/migration/status/${mid}`,
    advance:          (mid: string) => `${DEEPAGENTS_BASE_URL}/api/migration/advance/${mid}`,
    gatePreSemantic:  (mid: string) => `${DEEPAGENTS_BASE_URL}/api/migration/gate/pre-semantic/${mid}`,
    gateFieldMapping: (mid: string) => `${DEEPAGENTS_BASE_URL}/api/migration/gate/field-mapping/${mid}`,
    gateHierarchy:    (mid: string) => `${DEEPAGENTS_BASE_URL}/api/migration/gate/hierarchy/${mid}`,
    gateFinal:        (mid: string) => `${DEEPAGENTS_BASE_URL}/api/migration/gate/final/${mid}`,
    mappings:         (mid: string) => `${DEEPAGENTS_BASE_URL}/api/migration/mappings/${mid}`,
    list:             `${DEEPAGENTS_BASE_URL}/api/migration`,
  }

  // Azure deployment: only change NEXT_PUBLIC_DEEPAGENTS_URL.
  // svc-deepagents internally calls svc-ai-schema-mapper and svc-work-order-management
  // using its own .env (MIGRATION_BASE_URL, WO_MANAGEMENT_BASE_URL).
  // The frontend never calls those services directly.
}
```

---

## 5. Decision Guide — Which Pattern to Use

| Use case | Pattern | Endpoint |
|----------|---------|----------|
| Q&A, lookups, simple WO ops | A — One-shot | `POST /run` |
| Multi-step work, cross-domain | A — One-shot | `POST /run` |
| Schema mapper migration (gates surfaced conversationally by run_migration) | A — One-shot | `POST /run` |
| Workflows requiring LangGraph interrupt() gates (custom tools) | B — HITL Stateful | `POST /run-stateful` + `POST /resume/{id}` |
| Real-time progress UX during long tasks | C — WebSocket | `WS /ws/{session_id}` |
| Migration pipeline without chat (programmatic, all gates auto-approved) | Direct migration endpoints | `POST /api/migration/start` etc. |

---

## 6. Key Invariants to Know

- **The orchestrator is stateless by default.** Each `POST /run` call gets a fresh thread — no memory bleeds between requests unless you use `memory_set()` within a single call.
- **`run-stateful` uses `session_id` as the LangGraph thread ID.** The same `session_id` must be used in the matching `resume` call. Do not reuse session IDs across unrelated requests.
- **Migration uses Pattern A (one-shot) — no LangGraph interrupt().** `run_migration` drives the pipeline automatically, auto-advances step pauses, auto-confirms the write gate, and returns gate payloads as tool results. The orchestrator surfaces gates to the user through the normal chat reply.
- **Migration gates are conversational.** When `run_migration` returns `{status: "gate", gate_type: "field_mapping", payload: {...}}`, the orchestrator shows the payload in its chat reply and waits for the user's next message. The user's response triggers `submit_field_mapping`, then `run_migration` again. No `/resume` endpoint needed.
- **The write gate (Gate 3) is auto-confirmed by `run_migration`.** It executes automatically once hierarchy is approved. The user is never prompted for Gate 3 — it is intentionally invisible, matching the frontend behaviour.
- **The schema mapper pipeline writes nothing until the write gate passes.** Abandoning a migration before `submit_hierarchy` + the final `run_migration` call leaves no partial data in svc-ingestion. No rollback or cleanup is needed.
- **Sub-agents spawned by `task()` run without HITL.** They can call migration tools including `run_migration`, which works correctly without a checkpointer since migration HITL is conversational rather than interrupt-based.
- **Rate limit:** Both `POST /run` and `POST /run-stateful` are rate-limited to 20 requests/minute per IP by slowapi.
