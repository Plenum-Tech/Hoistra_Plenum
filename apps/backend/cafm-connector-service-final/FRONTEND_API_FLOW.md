# Frontend API Flow — CAFM AI Schema Mapper & Migration Pipelines

**Base URL:** `http://localhost:8003` (svc-ai-schema-mapper)

Both pipelines live in the same service. The Schema Mapper maps schemas
(structure-only). The Migration Ingestor maps + transforms + outputs the
actual data rows. Both are LangGraph state machines that pause at HITL gates
and node-by-node step boundaries.

---

## Core Polling Concept

Both pipelines follow the same poll-gate-advance loop:

```
Start job  →  Poll status  →  step_paused → Advance → Poll ...
                           →  awaiting_review → Submit gate decisions → Poll ...
                           →  complete → Show outputs
                           →  error → Show error
```

The status response always has:
- `status`            — `running | step_paused | awaiting_review | complete | error | ddl_failed`
- `pending_gate_type` — which gate is open (null when not at a gate)
- `pending_gate_payload` — what the gate is showing the user (null when not at a gate)
- `nodes[]`           — the full pipeline with per-node `status: pending | running | completed`

Poll interval: **2 seconds** while `status == "running"`.
No polling needed while `status == "step_paused"` or `"awaiting_review"` — those wait for user action.

---

---

# PIPELINE 1 — Schema Mapper

Maps an external CMMS schema (Fiix / YAML / SQL DDL) to the plenum_cafm canonical schema.
Produces a `JsonMapperConfig` stored in the database.

**9 nodes, 3 HITL gates.**

```
Node 0  Canonical Schema Fetch     (auto)
Node 1  Schema Ingestion           (auto)
Node 2  Deterministic Mapping      (auto)
Node 3  Gate 1: Pre-Semantic Review    ← HITL GATE (approve/reject T1 matches)
Node 4  Semantic Mapping           (auto)
Node 5  Gate 2: Field Mapping Review   ← HITL GATE (low-confidence + unmapped)
Node 6  Hierarchy Detection        (auto)
Node 7  Gate 3: Hierarchy Verify       ← HITL GATE (FK approval)
Node 8  Output Generation          (auto)
```

---

## Step 1 — Start a schema mapping session

### Option A: Fiix CMMS (live API fetch)
```
POST /api/schema-mapping
Content-Type: application/json

{
  "connector_type": "fiix",
  "external_cmms_name": "Fiix",
  "organization_id": "uuid",
  "fiix_subdomain": "yourcompany",
  "fiix_app_key": "...",
  "fiix_access_key": "...",
  "fiix_secret_key": "..."
}
```

### Option B: Upload schema definition (YAML / JSON / SQL DDL)
```
POST /api/schema-mapping
Content-Type: application/json

{
  "connector_type": "upload",
  "external_cmms_name": "Maximo",
  "organization_id": "uuid",
  "schema_content": "<raw YAML / JSON / DDL string>",
  "schema_source": "yaml_file",
  "schema_format": "yaml"
}
```

**Response (201):**
```json
{
  "schema_mapping_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running"
}
```

Store `schema_mapping_id`. Begin polling.

---

## Step 2 — Poll status

```
GET /api/schema-mapping/{schema_mapping_id}/status
```

**Response shape:**
```json
{
  "schema_mapping_id": "550e8400-...",
  "status": "running | step_paused | awaiting_review | complete | error",
  "current_node": 2,
  "progress_pct": 25.0,
  "external_cmms_name": "Maximo",
  "started_at": "2026-04-19T10:00:00Z",
  "completed_at": null,
  "stats": {
    "total_tables": 8,
    "total_fields": 142,
    "tier1_mapped": 98,
    "tier2_auto_mapped": 22,
    "tier2_flagged": 14,
    "unmapped": 8,
    "detected_fk_count": 5,
    "hierarchy_depth": 3,
    "mapping_coverage_pct": 84.5
  },
  "pending_gate_type": null,
  "pending_gate_payload": null,
  "error_message": null,
  "nodes": [
    {
      "node_id": 0, "node_name": "Canonical Schema Fetch",
      "status": "completed",
      "started_at": "...", "completed_at": "...", "duration_ms": 340,
      "output": { "canonical_fields_count": 38, "canonical_tables_count": 6 },
      "logs": ["Fetched 38 canonical fields from plenum_cafm"]
    },
    { "node_id": 1, "node_name": "Schema Ingestion", "status": "completed", ... },
    { "node_id": 2, "node_name": "Deterministic Mapping", "status": "running", ... },
    { "node_id": 3, "node_name": "Gate 1: Pre-Semantic Review", "status": "pending", ... },
    ...
  ]
}
```

**Frontend routing table:**

| `status`           | `pending_gate_type`  | What to do                                      |
|--------------------|---------------------|-------------------------------------------------|
| `running`          | null                | Keep polling every 2s                           |
| `step_paused`      | `"step_N_*"`        | Show node summary → user clicks Next → `advance`|
| `awaiting_review`  | `"pre_semantic"`    | Show Gate 1 (T1 approval) UI                    |
| `awaiting_review`  | `"field_mapping"`   | Show Gate 2 (field mapping) UI                  |
| `awaiting_review`  | `"hierarchy"`       | Show Gate 3 (hierarchy) UI                      |
| `complete`         | null                | Show completion + download links                |
| `error`            | null                | Show `error_message`                            |
| `ddl_failed`       | null                | Show DDL error → user fixes → retry-ddl         |

---

## Step 3 — Advance past step pauses (Nodes 0, 1, 2, 4, 6, 8)

When `status == "step_paused"`, the pipeline has finished an auto node and is waiting
for the user to review the node summary before continuing.

```
POST /api/schema-mapping/{schema_mapping_id}/advance
```

No body required. Returns `{ "status": "running" }`. Then resume polling.

---

## Gate 1 — Pre-Semantic Review (Node 3)

**When:** `status == "awaiting_review"` AND `pending_gate_type == "pre_semantic"`

`pending_gate_payload` contains:
```json
{
  "gate": "schema_pre_semantic",
  "total_reviewable": 12,
  "items_by_table": {
    "assets": [
      {
        "source_table": "assets",
        "source_field": "EQUIP_NUM",
        "target_field": "asset_code",
        "confidence": 0.94,
        "tier": "T1_regex",
        "rationale": "Matched pattern ^EQUIP.*",
        "sample_values": ["EQUIP-001", "EQUIP-002"]
      }
    ],
    "work_orders": [ ... ]
  },
  "instructions": "..."
}
```

**What to show:** Per-field cards with the matched target and confidence. User
ticks Approve or "Send to semantic matching" for each field.

T1_alias fields are NOT shown — they auto-pass.

**Submit decisions:**
```
POST /api/schema-mapping/{schema_mapping_id}/gate/field-mapping
Content-Type: application/json

{
  "decisions": [
    { "source_table": "assets",      "source_field": "EQUIP_NUM",   "decision": "approve"  },
    { "source_table": "assets",      "source_field": "FAULT_DESC",  "decision": "semantic" },
    { "source_table": "work_orders", "source_field": "WO_TYPE",     "decision": "approve"  }
  ]
}
```

Note: omitted fields default to `"approve"`. Response: `{ "status": "running" }`. Resume polling.

---

## Gate 2 — Field Mapping Review (Node 5)

**When:** `status == "awaiting_review"` AND `pending_gate_type == "field_mapping"`

`pending_gate_payload` contains low-confidence T2 mappings and completely unmapped fields.

```json
{
  "flagged_mappings": [
    {
      "source_field": "MAINT_TYPE", "source_table": "work_orders",
      "target_field": "maintenance_type", "confidence": 0.72,
      "tier": "T2_semantic", "suggestions": ["maintenance_type", "wo_type"]
    }
  ],
  "unmapped_fields": [
    {
      "source_field": "LEGACY_REF", "source_table": "assets",
      "sample_values": ["LR-001", "LR-002"]
    }
  ]
}
```

**Submit decisions:**
```
POST /api/schema-mapping/{schema_mapping_id}/gate/field-mapping
Content-Type: application/json

{
  "decisions": [
    { "action": "accept",      "source_field": "MAINT_TYPE",  "source_table": "work_orders" },
    { "action": "reject",      "source_field": "FAULT_CODE",  "source_table": "work_orders" },
    { "action": "override",    "source_field": "WO_DESC",     "source_table": "work_orders",
      "target_field": "wo_type", "rationale": "..." },

    { "action": "custom",      "source_field": "LEGACY_REF",  "source_table": "assets",
      "target_table": "assets", "custom_column_name": "legacy_ref",
      "data_type": "VARCHAR(50)", "nullable": true },

    { "action": "raw_metadata","source_field": "OLD_NOTES",   "source_table": "assets" },
    { "action": "skip",        "source_field": "TMP_FLAG",    "source_table": "assets" }
  ]
}
```

Resume polling after response.

---

## Gate 3 — Hierarchy Verification (Node 7)

**When:** `status == "awaiting_review"` AND `pending_gate_type == "hierarchy"`

`pending_gate_payload` contains detected FK relationships:
```json
{
  "detected_fks": [
    {
      "source_table": "assets", "source_column": "site_id",
      "target_table": "sites",  "target_column": "site_id",
      "relationship_type": "CONTAINMENT", "confidence": 0.91,
      "reasoning": "assets.site_id matches sites.site_id values (95%)"
    }
  ],
  "hierarchy_levels": { "sites": 0, "assets": 1, "work_orders": 2 },
  "structure": "sites → assets → work_orders"
}
```

**Submit decisions:**
```
POST /api/schema-mapping/{schema_mapping_id}/gate/hierarchy
Content-Type: application/json

{
  "approved_hierarchies": [
    { "source_table": "assets", "source_column": "site_id",
      "target_table": "sites",  "target_column": "site_id", "confirmed": true }
  ],
  "rejected_hierarchies": [
    { "source_table": "work_orders", "source_column": "asset_id",
      "target_table": "assets", "target_column": "asset_id", "confirmed": false }
  ]
}
```

Resume polling.

---

## Step 4 — Job complete

When `status == "complete"`, show:
- `stats.mapping_coverage_pct`
- `stats.tier1_mapped`, `tier2_auto_mapped`, `tier2_flagged`, `unmapped`
- `stats.detected_fk_count`, `stats.hierarchy_depth`
- Completed `nodes[]` with per-node duration, output, logs

---

## Error handling

| `status`     | Action                                                        |
|--------------|---------------------------------------------------------------|
| `error`      | Display `error_message`. Offer retry.                         |
| `ddl_failed` | Display DDL error from `pending_gate_payload`. Let user fix field definition then POST to `/retry-ddl` |

**Retry DDL:**
```
POST /api/schema-mapping/{schema_mapping_id}/retry-ddl
Content-Type: application/json

{
  "extra_fields_config": [
    {
      "source_field": "LEGACY_REF", "source_table": "assets",
      "storage_strategy": "custom",
      "target_table": "assets",
      "custom_column_name": "legacy_ref",
      "data_type": "VARCHAR(50)",
      "nullable": true,
      "user_approved": true
    }
  ]
}
```

---

## Additional read endpoints

```
GET /api/schema-mapping/{id}/mappings      — full list of all field mappings
GET /api/schema-mapping/{id}/unmapped      — unmapped fields only
GET /api/schema-mapping/{id}/audit-trail   — full audit log
GET /api/schema-mapping                    — list all sessions for org
GET /api/schema-mapping/{id}              — full job detail
```

---

---

# PIPELINE 2 — Migration Ingestor

Ingests actual data rows from a customer CSV/Excel file. Maps fields, cleans data,
detects hierarchy, and produces output artifacts (JSON, CSV, SQL, PDF report).

**9 nodes, 3 HITL gates.**

```
Node 1  File Ingestion             (auto)
Node 2  Deterministic Mapping      (auto)
Node 3  Gate 0: Pre-Semantic Review    ← HITL GATE (approve/reject T1 matches)
Node 4  Semantic Mapping           (auto)
Node 5  Gate 1: Field Mapping Review   ← HITL GATE (low-confidence + unmapped)
Node 6  Data Preprocessing         (auto)
Node 7  Hierarchy Detection        (auto)
Node 8  Gate 2: Hierarchy Verify       ← HITL GATE (FK approval)
Node 9  Output Generation          (auto)
```

---

## Step 1 — Start a migration job

### Option A: File URL (already uploaded to blob)
```
POST /api/migration
Content-Type: application/json

{
  "file_url": "https://plenumstorage.blob.core.windows.net/.../assets.csv",
  "cmms_name": "Maximo",
  "organization_id": "uuid"
}
```

### Option B: Upload file directly (multipart)
```
POST /api/migration/start-with-upload
Content-Type: multipart/form-data

file=@assets.csv
cmms_name=Maximo
organization_id=uuid
```

**Response (201):**
```json
{
  "migration_id": "7f3c9100-...",
  "status": "running"
}
```

Store `migration_id`. Begin polling.

---

## Step 2 — Poll status

```
GET /api/migration/{migration_id}/status
```

**Response shape:**
```json
{
  "migration_id": "7f3c9100-...",
  "status": "running",
  "progress_pct": 33.0,
  "current_step": 3,
  "cmms_name": "Maximo",
  "started_at": "2026-04-19T10:00:00Z",
  "completed_at": null,
  "t1_mapped_count": 45,
  "t2_auto_count": 12,
  "t2_human_count": 0,
  "unmapped_count": 8,
  "total_fields": 65,
  "output_json_url": null,
  "output_csv_url": null,
  "output_sql_url": null,
  "migration_report_url": null,
  "pending_gate_type": null,
  "pending_gate_payload": null,
  "error_message": null,
  "nodes": [
    { "node_id": 1, "node_name": "File Ingestion",       "status": "completed", "duration_ms": 820, ... },
    { "node_id": 2, "node_name": "Deterministic Mapping", "status": "completed", "duration_ms": 1240, ... },
    { "node_id": 3, "node_name": "Gate 0: Pre-Semantic Review", "status": "running", ... },
    { "node_id": 4, "node_name": "Semantic Mapping",      "status": "pending", ... },
    ...
  ]
}
```

**Frontend routing table:**

| `status`           | `pending_gate_type`     | What to do                                      |
|--------------------|------------------------|-------------------------------------------------|
| `running`          | null                   | Keep polling every 2s                           |
| `step_paused`      | `"step_N_*"`           | Show node summary → user clicks Next → `advance`|
| `awaiting_review`  | `"pre_semantic"`       | Show Gate 0 (T1 approval) UI                    |
| `awaiting_review`  | `"field_mapping"`      | Show Gate 1 (field mapping) UI                  |
| `awaiting_review`  | `"hierarchy"`          | Show Gate 2 (hierarchy) UI                      |
| `awaiting_review`  | `"final_confirmation"` | Show Gate 3 (final confirmation) UI             |
| `complete`         | null                   | Show outputs + download links                   |
| `error`            | null                   | Show `error_message`                            |

---

## Step 3 — Advance past step pauses (Nodes 1, 2, 4, 6, 7, 9)

```
POST /api/migration/{migration_id}/advance
```

No body. Returns `{ "status": "running" }`. Resume polling.

---

## Gate 0 — Pre-Semantic Review (Node 3)

**When:** `status == "awaiting_review"` AND `pending_gate_type == "pre_semantic"`

`pending_gate_payload` contains:
```json
{
  "gate": "pre_semantic",
  "total_reviewable": 18,
  "review_items_by_table": {
    "assets": [
      {
        "source_table": "assets",
        "source_field": "EQUIP_NUM",
        "target_field": "asset_code",
        "confidence": 0.93,
        "tier": "T1_exact",
        "rationale": "Exact match after normalisation",
        "sample_values": ["EQUIP-001", "EQUIP-002"]
      }
    ]
  },
  "instructions": "..."
}
```

**Submit decisions:**
```
POST /api/migration/{migration_id}/gate/pre-semantic
Content-Type: application/json

{
  "decisions": {
    "assets": [
      { "source_field": "EQUIP_NUM",  "decision": "approve"  },
      { "source_field": "FAULT_DESC", "decision": "semantic" }
    ],
    "work_orders": [
      { "source_field": "WO_TYPE", "decision": "approve" }
    ]
  }
}
```

`"approve"` keeps the T1 mapping. `"semantic"` sends it to semantic matching.
Omitted fields default to `"approve"`. Resume polling.

---

## Gate 1 — Field Mapping Review (Node 5)

**When:** `status == "awaiting_review"` AND `pending_gate_type == "field_mapping"`

Contains low-confidence T2 flagged mappings and completely unmapped fields.

**Submit decisions:**
```
POST /api/migration/{migration_id}/gate/field-mapping
Content-Type: application/json

{
  "flagged": {
    "assets": [
      { "action": "accept",   "source_field": "MAINT_TYPE" },
      { "action": "override", "source_field": "WO_DESC",
        "target_field": "wo_type", "rationale": "..." }
    ]
  },
  "unmapped": {
    "assets": [
      { "action": "custom",
        "source_field": "LEGACY_REF",
        "target_table": "assets",
        "custom_column_name": "legacy_ref",
        "data_type": "VARCHAR(50)",
        "nullable": true },
      { "action": "raw_metadata", "source_field": "OLD_NOTES" },
      { "action": "skip",         "source_field": "TMP_FLAG"  }
    ]
  }
}
```

Resume polling.

---

## Gate 2 — Hierarchy Verification (Node 8)

**When:** `status == "awaiting_review"` AND `pending_gate_type == "hierarchy"`

Contains validated FK relationships from the actual data rows.

**Submit decisions:**
```
POST /api/migration/{migration_id}/gate/hierarchy
Content-Type: application/json

{
  "confirmed_hierarchies": [
    {
      "source_table": "assets",    "source_column": "site_id",
      "target_table": "sites",     "target_column": "site_id",
      "customer_confirmed": true
    }
  ],
  "hierarchy_corrections": {}
}
```

Resume polling.

---

## Gate 3 — Final Confirmation (Node 9)

**When:** `status == "awaiting_review"` AND `pending_gate_type == "final_confirmation"`

This is the last gate before output files are written and handed to svc-ingestion.
Show a summary of all mappings and hierarchy, then:

```
POST /api/migration/{migration_id}/gate/final
Content-Type: application/json

{ "confirmed": true }
```

Resume polling. When `status == "complete"`, download links appear.

---

## Step 4 — Job complete

When `status == "complete"`, the response includes download URLs:
```json
{
  "output_json_url":       "/api/testing/artifacts/{id}/output.json",
  "output_csv_url":        "/api/testing/artifacts/{id}/output.csv",
  "output_sql_url":        "/api/testing/artifacts/{id}/output.sql",
  "migration_report_url":  "/api/testing/artifacts/{id}/report.pdf"
}
```

Also show the completed `nodes[]` panel with per-node timing, output counts, and logs.

---

## Additional read endpoints

```
GET /api/migration/{id}/status      — status + nodes[] (poll this)
GET /api/migration/{id}             — same as status (alias)
GET /api/migration/{id}/mappings    — full field mapping list
GET /api/migration/{id}/hierarchy   — confirmed hierarchy relationships
GET /api/migration/{id}/audit       — full event log
GET /api/migration/{id}/download/{format}  — download output artifact (json|csv|sql|pdf)
GET /api/migration/{id}/langsmith   — LangSmith trace URL for this run
GET /api/migration                  — list all migration jobs for org
```

---

---

# Complete Flow Diagrams

## Schema Mapper — Full Frontend Flow

```
POST /api/schema-mapping
          │
          ▼  schema_mapping_id
          
poll GET /status  ─── running ──────────────────────────────────────────┐
          │                                                              │
          ├─ step_paused (nodes 0,1,2,4,6,8)                            │
          │     Show node summary panel                                  │
          │     User clicks "Next Node →"                                │
          │     POST /advance                                            │
          │     └───────────────────────────────────────────────────────┘
          │
          ├─ awaiting_review + pre_semantic (Node 3)
          │     Render Gate 1 UI: per-field approve/semantic cards
          │     User makes decisions
          │     POST /gate/field-mapping   ← note: same endpoint for both gates
          │     └───────────────────────────────────────────────────────┘
          │
          ├─ awaiting_review + field_mapping (Node 5)
          │     Render Gate 2 UI: accept/reject/override/custom/raw/skip
          │     POST /gate/field-mapping
          │     └───────────────────────────────────────────────────────┘
          │
          ├─ awaiting_review + hierarchy (Node 7)
          │     Render Gate 3 UI: FK relationship confirm/reject
          │     POST /gate/hierarchy
          │     └───────────────────────────────────────────────────────┘
          │
          ├─ complete
          │     Show mapping stats, node timeline, no downloads (schema only)
          │
          ├─ error
          │     Show error_message
          │
          └─ ddl_failed
                Show which SQL failed
                User corrects data_type / column_name
                POST /retry-ddl
                └───────────────────────────────────────────────────────┘
```

## Migration Ingestor — Full Frontend Flow

```
POST /api/migration  (or /start-with-upload)
          │
          ▼  migration_id

poll GET /status  ─── running ──────────────────────────────────────────┐
          │                                                              │
          ├─ step_paused (nodes 1,2,4,6,7,9)                            │
          │     Show node summary panel                                  │
          │     POST /advance                                            │
          │     └───────────────────────────────────────────────────────┘
          │
          ├─ awaiting_review + pre_semantic (Node 3)
          │     Gate 0: T1 mapping approval
          │     POST /gate/pre-semantic
          │     └───────────────────────────────────────────────────────┘
          │
          ├─ awaiting_review + field_mapping (Node 5)
          │     Gate 1: Low-confidence + unmapped field decisions
          │     POST /gate/field-mapping
          │     └───────────────────────────────────────────────────────┘
          │
          ├─ awaiting_review + hierarchy (Node 8)
          │     Gate 2: FK relationship confirm/reject
          │     POST /gate/hierarchy
          │     └───────────────────────────────────────────────────────┘
          │
          ├─ awaiting_review + final_confirmation (Node 9)
          │     Gate 3: Final sign-off before write
          │     POST /gate/final
          │     └───────────────────────────────────────────────────────┘
          │
          ├─ complete
          │     Show download links: JSON, CSV, SQL, PDF report
          │     Show per-node timeline with durations
          │
          └─ error
                Show error_message
```

---

# Key Rules for Frontend

1. **Always read `pending_gate_type`** to know which gate UI to show.
   Never hard-code "if node 3 then show gate" — the graph can skip gates if nothing
   is reviewable (e.g. if all T1 matches are alias tier, pre-semantic gate auto-passes
   and `awaiting_review` never fires for it).

2. **`step_paused` ≠ `awaiting_review`.**
   `step_paused` = auto node finished, user reviews node output, clicks Next.
   `awaiting_review` = HITL gate open, user makes decisions, submits to gate endpoint.

3. **`pending_gate_payload` is your UI data source.**
   Don't fetch a separate endpoint for gate data — it's already in the status response.

4. **Omitted decisions default to approve.**
   If the user doesn't explicitly touch a field in a gate, it defaults to approved.
   You don't need to send every field — only the ones the user acted on.

5. **`nodes[]` drives your progress stepper.**
   Use `node.status` (`pending | running | completed`) to render the pipeline timeline.
   Use `node.output` and `node.logs` to populate node detail panels.
   Use `node.duration_ms` for timing display.

6. **Gate endpoints are the same URL for both pre-semantic and field-mapping gates**
   in the Schema Mapper pipeline — both POST to `/gate/field-mapping`. The backend
   differentiates by which gate is currently open (`pending_gate_type`).
   In the Migration pipeline, pre-semantic has its own endpoint `/gate/pre-semantic`.

---

---

# End-to-End Worked Examples

---

## Example A — Migration: Customer uploads a Maximo CSV file

**Scenario:** A customer exports their asset and work order data from IBM Maximo
as two CSV files zipped together. The operator uploads the file through the UI
and walks through the full migration pipeline to get the mapped + cleaned output.

The file contains columns like `ASSETNUM`, `SITEID`, `WORKORDERID`, `WOPRIORITY`,
`STATUSDATE`, `TARGCOMPDATE`, `VENDOR_CODE` (unmapped), `INTERNAL_REF` (junk).

---

### A1 — Upload the file and start the job

```
POST /api/migration/start-with-upload
Content-Type: multipart/form-data

file=@maximo_export.csv          (the CSV file binary)
cmms_name=Maximo
organization_id=11111111-1111-1111-1111-111111111111
```

**Response 201:**
```json
{
  "migration_id": "aab12300-dead-beef-0000-111111111111",
  "status": "running"
}
```

Store `migration_id = "aab12300-dead-beef-0000-111111111111"`. Start polling.

---

### A2 — Poll: Node 1 running (File Ingestion)

```
GET /api/migration/aab12300-dead-beef-0000-111111111111/status
```

```json
{
  "status": "running",
  "current_step": 1,
  "progress_pct": 11.0,
  "pending_gate_type": null,
  "nodes": [
    { "node_id": 1, "node_name": "File Ingestion", "status": "running" },
    { "node_id": 2, "node_name": "Deterministic Mapping", "status": "pending" },
    ...
  ]
}
```

Action: keep polling every 2 seconds.

---

### A3 — Poll: Node 1 done — step_paused

```json
{
  "status": "step_paused",
  "current_step": 1,
  "progress_pct": 11.0,
  "pending_gate_type": "step_1_ingest",
  "pending_gate_payload": {
    "node": 1,
    "label": "File Ingestion",
    "row_count": 1240,
    "column_count": 18,
    "tables": ["assets", "work_orders"],
    "detected_format": "csv"
  },
  "nodes": [
    {
      "node_id": 1, "node_name": "File Ingestion", "status": "completed",
      "duration_ms": 830,
      "output": { "row_count": 1240, "column_count": 18, "table_count": 2 },
      "logs": ["Parsed 1240 rows × 18 columns", "Detected format: csv", "EL-M.1: PASSED"]
    },
    { "node_id": 2, "node_name": "Deterministic Mapping", "status": "pending" },
    ...
  ]
}
```

Action: show the node summary card (1240 rows, 2 tables detected). User clicks "Next Node →".

```
POST /api/migration/aab12300-dead-beef-0000-111111111111/advance
```

```json
{ "status": "running" }
```

Resume polling.

---

### A4 — Poll: Node 2 done — step_paused (Deterministic Mapping)

```json
{
  "status": "step_paused",
  "current_step": 2,
  "progress_pct": 22.0,
  "pending_gate_type": "step_2_deterministic",
  "pending_gate_payload": {
    "node": 2,
    "label": "Deterministic Mapping",
    "t1_mapped": 14,
    "unresolved": 4,
    "coverage_pct": 78.0
  },
  "t1_mapped_count": 14,
  "total_fields": 18,
  "nodes": [
    { "node_id": 1, "status": "completed", ... },
    {
      "node_id": 2, "node_name": "Deterministic Mapping", "status": "completed",
      "duration_ms": 1350,
      "output": { "tier1_mapped": 14, "unresolved": 4, "coverage_pct": 77.8 },
      "logs": [
        "T1_exact: ASSETNUM → asset_code (0.99)",
        "T1_exact: SITEID → location_code (0.99)",
        "T1_alias: WOPRIORITY → wo_priority (0.97)",
        "T1_regex: STATUSDATE → wo_status (0.91)",
        "Unresolved: VENDOR_CODE, INTERNAL_REF, TARGCOMPDATE, FAULTDESC"
      ]
    },
    { "node_id": 3, "status": "pending" },
    ...
  ]
}
```

Action: show node summary (14 matched, 4 still to resolve). User clicks "Next Node →".

```
POST /api/migration/aab12300-dead-beef-0000-111111111111/advance
```

Resume polling.

---

### A5 — Poll: awaiting_review — Gate 0: Pre-Semantic Review (Node 3)

```json
{
  "status": "awaiting_review",
  "current_step": 3,
  "progress_pct": 33.0,
  "pending_gate_type": "pre_semantic",
  "pending_gate_payload": {
    "gate": "pre_semantic",
    "migration_id": "aab12300-dead-beef-0000-111111111111",
    "total_reviewable": 3,
    "review_items_by_table": {
      "assets": [
        {
          "source_table": "assets",
          "source_field": "ASSETNUM",
          "target_field": "asset_code",
          "confidence": 0.99,
          "tier": "T1_exact",
          "rationale": "Exact match after normalisation",
          "sample_values": ["MOB-AHU-001", "MOB-CH-001", "MOB-BLR-001"]
        },
        {
          "source_table": "assets",
          "source_field": "STATUSDATE",
          "target_field": "wo_status",
          "confidence": 0.91,
          "tier": "T1_regex",
          "rationale": "Matched pattern .*DATE.* → date-type canonical field",
          "sample_values": ["2026-01-15", "2026-02-03"]
        }
      ],
      "work_orders": [
        {
          "source_table": "work_orders",
          "source_field": "WOPRIORITY",
          "target_field": "wo_priority",
          "confidence": 0.97,
          "tier": "T1_alias",
          "rationale": "Alias: WOPRIORITY → wo_priority in Maximo alias table",
          "sample_values": ["Highest", "High", "Medium"]
        }
      ]
    },
    "instructions": "Review each deterministically matched field..."
  }
}
```

Note: `WOPRIORITY` is T1_alias — it already auto-passed and appears here for info only.
Only `ASSETNUM` (T1_exact) and `STATUSDATE` (T1_regex) are shown as reviewable.

Action: render Gate 0 UI. The operator sees ASSETNUM → asset_code looks correct, approves it.
STATUSDATE → wo_status looks wrong (STATUSDATE is a date, not a status field) — sends to semantic.

```
POST /api/migration/aab12300-dead-beef-0000-111111111111/gate/pre-semantic
Content-Type: application/json

{
  "decisions": {
    "assets": [
      { "source_field": "ASSETNUM",   "decision": "approve"  },
      { "source_field": "STATUSDATE", "decision": "semantic" }
    ]
  }
}
```

```json
{ "status": "running" }
```

Resume polling. `STATUSDATE` now joins the 4 previously unresolved fields in semantic matching.

---

### A6 — Poll: Node 4 done — step_paused (Semantic Mapping)

```json
{
  "status": "step_paused",
  "current_step": 4,
  "progress_pct": 44.0,
  "pending_gate_type": "step_4_semantic",
  "pending_gate_payload": {
    "node": 4,
    "label": "Semantic Mapping",
    "t2_auto_mapped": 2,
    "t2_flagged": 2,
    "unmappable": 1
  },
  "nodes": [
    ...
    {
      "node_id": 4, "node_name": "Semantic Mapping", "status": "completed",
      "duration_ms": 3200,
      "output": { "tier2_auto_mapped": 2, "tier2_flagged": 2, "unmappable": 1 },
      "logs": [
        "STATUSDATE → created_at (0.87, auto-accepted)",
        "TARGCOMPDATE → due_date (0.86, auto-accepted)",
        "FAULTDESC → wo_type (0.71, flagged for review)",
        "VENDOR_CODE → supplier (0.67, flagged for review)",
        "INTERNAL_REF → unmappable (0.38)"
      ]
    },
    { "node_id": 5, "status": "pending" },
    ...
  ]
}
```

Action: show node summary. User clicks "Next Node →".

```
POST /api/migration/aab12300-dead-beef-0000-111111111111/advance
```

Resume polling.

---

### A7 — Poll: awaiting_review — Gate 1: Field Mapping Review (Node 5)

```json
{
  "status": "awaiting_review",
  "pending_gate_type": "field_mapping",
  "pending_gate_payload": {
    "flagged_by_table": {
      "work_orders": [
        {
          "source_field": "FAULTDESC", "source_table": "work_orders",
          "target_field": "wo_type", "confidence": 0.71,
          "tier": "T2_semantic",
          "suggestions": ["wo_type", "maintenance_type", "wo_status"],
          "sample_values": ["Electrical fault", "Mechanical failure", "Routine check"]
        },
        {
          "source_field": "VENDOR_CODE", "source_table": "work_orders",
          "target_field": "supplier", "confidence": 0.67,
          "tier": "T2_semantic",
          "suggestions": ["supplier", "user_name"],
          "sample_values": ["VND-001", "VND-027"]
        }
      ]
    },
    "unmapped_by_table": {
      "assets": [
        {
          "source_field": "INTERNAL_REF", "source_table": "assets",
          "sample_values": ["IR-2023-001", "IR-2023-002"]
        }
      ]
    }
  }
}
```

Action: render Gate 1 UI. Operator decisions:
- `FAULTDESC` → override to `maintenance_type` (better fit than `wo_type`)
- `VENDOR_CODE` → accept `supplier` as-is
- `INTERNAL_REF` → skip (internal junk field, not needed)

```
POST /api/migration/aab12300-dead-beef-0000-111111111111/gate/field-mapping
Content-Type: application/json

{
  "flagged": {
    "work_orders": [
      {
        "action": "override",
        "source_field": "FAULTDESC",
        "target_field": "maintenance_type",
        "rationale": "Fault description maps better to maintenance_type than wo_type"
      },
      {
        "action": "accept",
        "source_field": "VENDOR_CODE"
      }
    ]
  },
  "unmapped": {
    "assets": [
      { "action": "skip", "source_field": "INTERNAL_REF" }
    ]
  }
}
```

Resume polling.

---

### A8 — Poll: Nodes 6 and 7 run automatically

Nodes 6 (Data Preprocessing) and 7 (Hierarchy Detection) run without gates.
Each produces a `step_paused` stop. Operator clicks "Next Node →" twice.

**Node 6 step_paused payload example:**
```json
{
  "pending_gate_type": "step_6_preprocess",
  "pending_gate_payload": {
    "label": "Data Preprocessing",
    "rows_cleaned": 1234,
    "warnings": 2,
    "warning_messages": [
      "assets: Dropped 6 duplicate rows",
      "work_orders: Coerced 3 date columns to ISO 8601"
    ]
  }
}
```

**Node 7 step_paused payload example:**
```json
{
  "pending_gate_type": "step_7_hierarchy",
  "pending_gate_payload": {
    "label": "Hierarchy Detection",
    "hierarchies": 2,
    "hierarchy_levels": { "sites": 0, "assets": 1, "work_orders": 2 }
  }
}
```

Two `POST /advance` calls. Resume polling after each.

---

### A9 — Poll: awaiting_review — Gate 2: Hierarchy Verification (Node 8)

```json
{
  "status": "awaiting_review",
  "pending_gate_type": "hierarchy",
  "pending_gate_payload": {
    "hierarchies_to_review": [
      {
        "source_table": "assets",     "source_column": "SITEID",
        "target_table": "sites",      "target_column": "SITEID",
        "relationship_type": "CONTAINMENT",
        "confidence": 0.96,
        "data_match_rate": "98%",
        "reasoning": "assets.SITEID values match 98% of sites.SITEID — strong containment"
      },
      {
        "source_table": "work_orders", "source_column": "ASSETNUM",
        "target_table": "assets",      "target_column": "ASSETNUM",
        "relationship_type": "CONTAINMENT",
        "confidence": 0.93,
        "data_match_rate": "91%",
        "reasoning": "work_orders.ASSETNUM matches 91% of assets.ASSETNUM"
      }
    ],
    "proposed_structure": "sites → assets → work_orders"
  }
}
```

Operator confirms both — the hierarchy looks correct.

```
POST /api/migration/aab12300-dead-beef-0000-111111111111/gate/hierarchy
Content-Type: application/json

{
  "confirmed_hierarchies": [
    {
      "source_table": "assets",      "source_column": "SITEID",
      "target_table": "sites",       "target_column": "SITEID",
      "customer_confirmed": true
    },
    {
      "source_table": "work_orders", "source_column": "ASSETNUM",
      "target_table": "assets",      "target_column": "ASSETNUM",
      "customer_confirmed": true
    }
  ],
  "hierarchy_corrections": {}
}
```

Resume polling.

---

### A10 — Poll: awaiting_review — Gate 3: Final Confirmation (Node 9)

```json
{
  "status": "awaiting_review",
  "pending_gate_type": "final_confirmation",
  "pending_gate_payload": {
    "summary": {
      "total_fields": 18,
      "t1_mapped": 13,
      "t2_auto_mapped": 2,
      "t2_human_reviewed": 2,
      "skipped": 1,
      "mapping_coverage_pct": 94.4,
      "hierarchy": "sites → assets → work_orders",
      "rows_to_write": 1234
    }
  }
}
```

Action: show a final summary screen. Operator clicks "Confirm & Generate Output".

```
POST /api/migration/aab12300-dead-beef-0000-111111111111/gate/final
Content-Type: application/json

{ "confirmed": true }
```

Resume polling.

---

### A11 — Poll: Node 9 step_paused (Output Generation)

```json
{
  "status": "step_paused",
  "pending_gate_type": "step_9_output",
  "pending_gate_payload": {
    "label": "Output Generation",
    "artifacts_uploaded": 4,
    "formats": ["json", "csv", "sql", "pdf"]
  }
}
```

User clicks "Next Node →".

```
POST /api/migration/aab12300-dead-beef-0000-111111111111/advance
```

---

### A12 — Poll: complete

```json
{
  "status": "complete",
  "progress_pct": 100.0,
  "t1_mapped_count": 13,
  "t2_auto_count": 2,
  "t2_human_count": 2,
  "unmapped_count": 1,
  "total_fields": 18,
  "output_json_url":      "/api/testing/artifacts/aab12300-.../output.json",
  "output_csv_url":       "/api/testing/artifacts/aab12300-.../output.csv",
  "output_sql_url":       "/api/testing/artifacts/aab12300-.../output.sql",
  "migration_report_url": "/api/testing/artifacts/aab12300-.../report.pdf",
  "nodes": [
    { "node_id": 1, "status": "completed", "duration_ms": 830  },
    { "node_id": 2, "status": "completed", "duration_ms": 1350 },
    { "node_id": 3, "status": "completed", "duration_ms": 47200 },
    { "node_id": 4, "status": "completed", "duration_ms": 3200  },
    { "node_id": 5, "status": "completed", "duration_ms": 28900 },
    { "node_id": 6, "status": "completed", "duration_ms": 680   },
    { "node_id": 7, "status": "completed", "duration_ms": 4100  },
    { "node_id": 8, "status": "completed", "duration_ms": 19300 },
    { "node_id": 9, "status": "completed", "duration_ms": 5600  }
  ]
}
```

Action: render completion screen with download buttons for JSON / CSV / SQL / PDF.
Total wall-clock time: ~2 min including 3 gate waits.

---

---

## Example B — Schema Mapper: Connect to Fiix CMMS and map its schema

**Scenario:** A customer is migrating from Fiix CMMS. The operator enters Fiix
API credentials in the UI. The schema mapper fetches the Fiix field registry live,
maps it to plenum_cafm canonical fields, detects the sites → assets → work orders
hierarchy, and produces a `JsonMapperConfig` that will be used for all future
Fiix data ingestion.

Fiix schema has tables: `Assets`, `WorkOrders`, `Locations`, `Parts`, `Users`.
It uses Fiix-specific field names like `intAssetID`, `strName`, `intSiteID`,
`intWorkOrderStatusID`, `dtmDateCreated`.

---

### B1 — Start schema mapping session with Fiix credentials

```
POST /api/schema-mapping
Content-Type: application/json

{
  "connector_type": "fiix",
  "external_cmms_name": "Fiix",
  "organization_id": "22222222-2222-2222-2222-222222222222",
  "fiix_subdomain": "plenum",
  "fiix_app_key": "abc123appkey",
  "fiix_access_key": "accesskey456",
  "fiix_secret_key": "secretkey789"
}
```

**Response 201:**
```json
{
  "schema_mapping_id": "ff1a0000-f11x-0000-cafe-babe00000001",
  "status": "running"
}
```

Store `schema_mapping_id`. Begin polling.

---

### B2 — Poll: Nodes 0 and 1 running (auto)

Node 0 fetches the canonical plenum_cafm schema from the database.
Node 1 calls the Fiix API and parses the mapper config.

Each produces a `step_paused`. Operator clicks "Next Node →" twice.

**Node 0 step_paused:**
```json
{
  "status": "step_paused",
  "pending_gate_type": "step_0_canonical",
  "pending_gate_payload": {
    "label": "Canonical Schema Fetch",
    "canonical_table_count": 6,
    "canonical_column_count": 38
  }
}
```

**Node 1 step_paused:**
```json
{
  "status": "step_paused",
  "pending_gate_type": "step_1_ingest",
  "pending_gate_payload": {
    "label": "Schema Ingestion",
    "source": "fiix_api",
    "table_count": 5,
    "total_columns": 87,
    "tables_found": ["Assets", "WorkOrders", "Locations", "Parts", "Users"]
  }
}
```

Two `POST /advance` calls. Resume polling after each.

---

### B3 — Poll: Node 2 done — step_paused (Deterministic Mapping)

```json
{
  "status": "step_paused",
  "pending_gate_type": "step_2_deterministic",
  "pending_gate_payload": {
    "label": "Deterministic Mapping",
    "t1_mapped": 61,
    "unresolved": 26,
    "coverage_pct": 70.1
  },
  "nodes": [
    ...
    {
      "node_id": 2, "node_name": "Deterministic Mapping", "status": "completed",
      "duration_ms": 2100,
      "output": { "tier1_mapped": 61, "unresolved": 26, "coverage_pct": 70.1 },
      "logs": [
        "T1_alias: intAssetID → asset_code (Fiix registry alias, 0.98)",
        "T1_alias: strName → asset_name (Fiix registry alias, 0.98)",
        "T1_alias: intSiteID → location_code (Fiix registry alias, 0.97)",
        "T1_alias: intWorkOrderStatusID → wo_status (Fiix registry alias, 0.96)",
        "T1_exact: dtmDateCreated → created_at (0.99)",
        "T1_regex: strSerialNumber → serial (pattern match, 0.92)",
        "T1_regex: strBarcode → asset_code (pattern match, 0.90)",
        "Unresolved: 26 fields (intCustomField1..10, strNotes, oobjWorkOrderType, ...)"
      ]
    },
    ...
  ]
}
```

Operator clicks "Next Node →".

```
POST /api/schema-mapping/ff1a0000-f11x-0000-cafe-babe00000001/advance
```

Resume polling.

---

### B4 — Poll: awaiting_review — Gate 1: Pre-Semantic Review (Node 3)

Many Fiix matches are T1_alias (auto-passed). Only the regex and exact matches
that need human sign-off appear here.

```json
{
  "status": "awaiting_review",
  "pending_gate_type": "pre_semantic",
  "pending_gate_payload": {
    "gate": "schema_pre_semantic",
    "schema_mapping_id": "ff1a0000-f11x-0000-cafe-babe00000001",
    "total_reviewable": 5,
    "items_by_table": {
      "Assets": [
        {
          "source_table": "Assets",
          "source_field": "dtmDateCreated",
          "target_field": "created_at",
          "confidence": 0.99,
          "tier": "T1_exact",
          "rationale": "Exact match after normalisation",
          "sample_values": ["2024-01-15T08:30:00", "2024-02-03T14:22:00"]
        },
        {
          "source_table": "Assets",
          "source_field": "strBarcode",
          "target_field": "asset_code",
          "confidence": 0.90,
          "tier": "T1_regex",
          "rationale": "Pattern .*[Bb]arcode.* → asset_code",
          "sample_values": ["BC-MOB-001", "BC-MOB-002"]
        },
        {
          "source_table": "Assets",
          "source_field": "strSerialNumber",
          "target_field": "serial",
          "confidence": 0.92,
          "tier": "T1_regex",
          "rationale": "Pattern .*[Ss]erial.* → serial",
          "sample_values": ["SN-2023-0041", "SN-2023-0042"]
        }
      ],
      "WorkOrders": [
        {
          "source_table": "WorkOrders",
          "source_field": "dtmDateCreated",
          "target_field": "created_at",
          "confidence": 0.99,
          "tier": "T1_exact",
          "rationale": "Exact match after normalisation",
          "sample_values": ["2025-11-01T09:00:00"]
        },
        {
          "source_table": "WorkOrders",
          "source_field": "dtmDateCompleted",
          "target_field": "created_at",
          "confidence": 0.88,
          "tier": "T1_regex",
          "rationale": "Pattern .*[Dd]ate.* → created_at (closest match)",
          "sample_values": ["2025-11-15T17:30:00", null]
        }
      ]
    }
  }
}
```

Operator review:
- `Assets.dtmDateCreated` → `created_at` ✓ approve
- `Assets.strBarcode` → `asset_code` — wrong, barcode ≠ asset code, send to semantic
- `Assets.strSerialNumber` → `serial` ✓ approve
- `WorkOrders.dtmDateCreated` → `created_at` ✓ approve
- `WorkOrders.dtmDateCompleted` → `created_at` — wrong, this is a completion date not created, send to semantic

```
POST /api/schema-mapping/ff1a0000-f11x-0000-cafe-babe00000001/gate/field-mapping
Content-Type: application/json

{
  "decisions": [
    { "source_table": "Assets",     "source_field": "dtmDateCreated",   "decision": "approve"  },
    { "source_table": "Assets",     "source_field": "strBarcode",        "decision": "semantic" },
    { "source_table": "Assets",     "source_field": "strSerialNumber",   "decision": "approve"  },
    { "source_table": "WorkOrders", "source_field": "dtmDateCreated",    "decision": "approve"  },
    { "source_table": "WorkOrders", "source_field": "dtmDateCompleted",  "decision": "semantic" }
  ]
}
```

Resume polling. `strBarcode` and `dtmDateCompleted` now go through semantic matching.

---

### B5 — Poll: Node 4 done — step_paused (Semantic Mapping)

```json
{
  "status": "step_paused",
  "pending_gate_type": "step_4_semantic",
  "pending_gate_payload": {
    "label": "Semantic Mapping",
    "t2_auto_mapped": 18,
    "t2_flagged": 7,
    "unmappable": 3
  },
  "nodes": [
    ...
    {
      "node_id": 4, "status": "completed", "duration_ms": 5800,
      "output": { "tier2_auto_mapped": 18, "tier2_flagged": 7, "unmappable": 3 },
      "logs": [
        "strBarcode → asset_code rejected from T1, semantic score 0.51 → unmappable",
        "dtmDateCompleted → completed_at (0.89, auto-accepted)",
        "oobjWorkOrderType → wo_type (0.86, auto-accepted)",
        "intCustomField1 → unmappable (0.31)",
        "intCustomField2 → unmappable (0.29)",
        "strNotes → unmappable (0.41)",
        "strPONumber → wo_code (0.72, flagged for review)",
        "intMeterReading → unmappable (0.22)",
        "... 7 more flagged at 0.65-0.85"
      ]
    },
    ...
  ]
}
```

Operator clicks "Next Node →".

```
POST /api/schema-mapping/ff1a0000-f11x-0000-cafe-babe00000001/advance
```

Resume polling.

---

### B6 — Poll: awaiting_review — Gate 2: Field Mapping Review (Node 5)

```json
{
  "status": "awaiting_review",
  "pending_gate_type": "field_mapping",
  "pending_gate_payload": {
    "flagged_mappings": [
      {
        "source_field": "strPONumber", "source_table": "WorkOrders",
        "target_field": "wo_code",     "confidence": 0.72,
        "tier": "T2_semantic",
        "suggestions": ["wo_code", "part_code", "sm_code"],
        "sample_values": ["PO-2025-001", "PO-2025-002"]
      },
      {
        "source_field": "intMeterReading", "source_table": "Assets",
        "target_field": "serial",          "confidence": 0.68,
        "tier": "T2_semantic",
        "suggestions": ["serial", "stock_on_hand"],
        "sample_values": [1042, 5230, 892]
      }
    ],
    "unmapped_fields": [
      {
        "source_field": "strBarcode",   "source_table": "Assets",
        "sample_values": ["BC-MOB-001", "BC-MOB-002"]
      },
      {
        "source_field": "strNotes",     "source_table": "WorkOrders",
        "sample_values": ["Follow up required", "Parts ordered"]
      },
      {
        "source_field": "intCustomField1", "source_table": "Assets",
        "sample_values": [100, 200, 300]
      },
      {
        "source_field": "intCustomField2", "source_table": "Assets",
        "sample_values": [1, 0, 1]
      },
      {
        "source_field": "intMeterReading", "source_table": "Assets",
        "sample_values": [1042, 5230]
      }
    ]
  }
}
```

Operator decisions:
- `strPONumber` → reject (PO number is not a WO code)
- `intMeterReading` (flagged) → reject
- `strBarcode` → add as custom column `barcode` on assets table
- `strNotes` → store in raw_metadata JSONB
- `intCustomField1`, `intCustomField2` → skip (internal Fiix fields, no value)
- `intMeterReading` (unmapped) → add as custom column `meter_reading` on assets

```
POST /api/schema-mapping/ff1a0000-f11x-0000-cafe-babe00000001/gate/field-mapping
Content-Type: application/json

{
  "decisions": [
    { "action": "reject",  "source_field": "strPONumber",      "source_table": "WorkOrders" },
    { "action": "reject",  "source_field": "intMeterReading",  "source_table": "Assets" },

    { "action": "custom",
      "source_field": "strBarcode", "source_table": "Assets",
      "target_table": "assets",
      "custom_column_name": "barcode",
      "data_type": "VARCHAR(100)",
      "nullable": true },

    { "action": "raw_metadata",
      "source_field": "strNotes", "source_table": "WorkOrders" },

    { "action": "skip", "source_field": "intCustomField1", "source_table": "Assets" },
    { "action": "skip", "source_field": "intCustomField2", "source_table": "Assets" },

    { "action": "custom",
      "source_field": "intMeterReading", "source_table": "Assets",
      "target_table": "assets",
      "custom_column_name": "meter_reading",
      "data_type": "INTEGER",
      "nullable": true }
  ]
}
```

Resume polling.

---

### B7 — Poll: Node 6 done — step_paused (Hierarchy Detection)

```json
{
  "status": "step_paused",
  "pending_gate_type": "step_6_hierarchy",
  "pending_gate_payload": {
    "label": "Hierarchy Detection",
    "hierarchies": 3,
    "cycles": 0,
    "hierarchy_levels": {
      "Locations": 0,
      "Assets":    1,
      "WorkOrders": 2,
      "Parts":     1
    }
  }
}
```

Operator clicks "Next Node →".

```
POST /api/schema-mapping/ff1a0000-f11x-0000-cafe-babe00000001/advance
```

Resume polling.

---

### B8 — Poll: awaiting_review — Gate 3: Hierarchy Verification (Node 7)

```json
{
  "status": "awaiting_review",
  "pending_gate_type": "hierarchy",
  "pending_gate_payload": {
    "detected_fks": [
      {
        "source_table": "Assets",    "source_column": "intSiteID",
        "target_table": "Locations", "target_column": "intSiteID",
        "relationship_type": "CONTAINMENT", "confidence": 0.97,
        "reasoning": "Assets.intSiteID matches 97% of Locations.intSiteID"
      },
      {
        "source_table": "WorkOrders", "source_column": "intAssetID",
        "target_table": "Assets",     "target_column": "intAssetID",
        "relationship_type": "CONTAINMENT", "confidence": 0.94,
        "reasoning": "WorkOrders.intAssetID matches 94% of Assets.intAssetID"
      },
      {
        "source_table": "WorkOrders", "source_column": "intSiteID",
        "target_table": "Locations",  "target_column": "intSiteID",
        "relationship_type": "REFERENCE", "confidence": 0.89,
        "reasoning": "Lateral reference — WO also holds its own site context"
      }
    ],
    "hierarchy_levels": { "Locations": 0, "Assets": 1, "WorkOrders": 2 },
    "structure": "Locations → Assets → WorkOrders"
  }
}
```

Operator approves all three — the Fiix hierarchy is correct.

```
POST /api/schema-mapping/ff1a0000-f11x-0000-cafe-babe00000001/gate/hierarchy
Content-Type: application/json

{
  "approved_hierarchies": [
    { "source_table": "Assets",     "source_column": "intSiteID",
      "target_table": "Locations",  "target_column": "intSiteID",  "confirmed": true },
    { "source_table": "WorkOrders", "source_column": "intAssetID",
      "target_table": "Assets",     "target_column": "intAssetID", "confirmed": true },
    { "source_table": "WorkOrders", "source_column": "intSiteID",
      "target_table": "Locations",  "target_column": "intSiteID",  "confirmed": true }
  ],
  "rejected_hierarchies": []
}
```

Resume polling.

---

### B9 — Poll: Node 8 done — step_paused (Output Generation)

```json
{
  "status": "step_paused",
  "pending_gate_type": "step_8_output",
  "pending_gate_payload": {
    "label": "Output Generation",
    "canonical_fields_count": 38,
    "total_source_fields": 87,
    "tier1_auto_mapped": 61,
    "tier2_auto_mapped": 18,
    "tier2_flagged": 7,
    "unmappable": 1,
    "mapping_coverage_pct": 90.8,
    "detected_fk_count": 3,
    "max_hierarchy_depth": 2
  }
}
```

Operator clicks "Next Node →".

```
POST /api/schema-mapping/ff1a0000-f11x-0000-cafe-babe00000001/advance
```

---

### B10 — Poll: complete

```json
{
  "status": "complete",
  "current_node": 8,
  "progress_pct": 100.0,
  "stats": {
    "total_tables": 5,
    "total_fields": 87,
    "tier1_mapped": 61,
    "tier2_auto_mapped": 18,
    "tier2_flagged": 7,
    "unmapped": 1,
    "detected_fk_count": 3,
    "hierarchy_depth": 2,
    "mapping_coverage_pct": 90.8
  },
  "nodes": [
    { "node_id": 0, "status": "completed", "duration_ms": 210  },
    { "node_id": 1, "status": "completed", "duration_ms": 1840 },
    { "node_id": 2, "status": "completed", "duration_ms": 2100 },
    { "node_id": 3, "status": "completed", "duration_ms": 52300 },
    { "node_id": 4, "status": "completed", "duration_ms": 5800  },
    { "node_id": 5, "status": "completed", "duration_ms": 34100 },
    { "node_id": 6, "status": "completed", "duration_ms": 3900  },
    { "node_id": 7, "status": "completed", "duration_ms": 21700 },
    { "node_id": 8, "status": "completed", "duration_ms": 680   }
  ]
}
```

Action: show completion screen. The `JsonMapperConfig` is now stored in the database
and will be used automatically whenever Fiix data is ingested for this organisation.
No download links for schema mapping — the output is a DB record, not a file.

To read the full mapping config:
```
GET /api/schema-mapping/ff1a0000-f11x-0000-cafe-babe00000001/mappings
GET /api/schema-mapping/ff1a0000-f11x-0000-cafe-babe00000001/audit-trail
```
