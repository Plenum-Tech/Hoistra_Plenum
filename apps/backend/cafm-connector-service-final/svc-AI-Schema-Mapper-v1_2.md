# svc-AI-Schema-Mapper — Architecture & Implementation Spec

> Universal AI-native CMMS migration pipeline.  
> Converts any customer CMMS export (CSV, Excel, structured documents) into a
> validated, hierarchically resolved JSON output that maps to the `plenum_cafm`
> target schema — without hardcoded per-platform mappings.  
> **Last updated:** April 2026 (v1.2 — LangSmith observability added throughout)

---

## 1. What This Service Does

Traditional CMMS migration requires a new hardcoded mapping table for every
source platform (Maximo, SAP PM, Fiix, Archibus, etc.). Each new customer with
a different CMMS means weeks of manual mapping work.

`svc-AI-Schema-Mapper` replaces all of that with a single universal pipeline:

1. **Customer uploads** their CMMS export (CSV/Excel) + names their source platform
2. **Pipeline auto-detects** column structure, data types, encoding, delimiter
3. **3-tier mapping cascade** resolves every source field to a canonical target field
4. **Hierarchy detection** reconstructs `sites → locations → assets → work orders → tasks`
5. **Human review gates** surface only the ambiguous cases — not everything
6. **Validated JSON** is exported and handed to `svc-ingestion` for DB write

The customer sees every step as it executes, in real-time, with rationale for
every mapping decision.

---

## 2. Where This Sits in the Platform

```
Customer CMMS Export (CSV / Excel)
              │
              ▼
┌─────────────────────────────────────────────────────┐
│         svc-AI-Schema-Mapper  (port 8003)           │
│                                                     │
│  LangGraph compiled graph — Postgres checkpointer   │
│  3-tier mapping + hierarchy + human review + export │
└───────────────────────┬─────────────────────────────┘
                        │  IntermediateSchema (existing contract)
                        ▼
┌─────────────────────────────────────────────────────┐
│         svc-ingestion  (port 8001)                  │
│  Stage 3 eval → Stage 4 unifier → plenum_cafm DB   │
└─────────────────────────────────────────────────────┘
```

**Key principle:** `svc-AI-Schema-Mapper` outputs the same `IntermediateSchema`
Pydantic model already defined in `svc-ingestion/src/shared/intermediate_schema.py`.
The existing eval layers (EL-2.1, EL-2.2, EL-2.3), unifier, and audit receipts
work unchanged. This service is purely a pre-ingestion translation layer.

---

## 2a. LangSmith Observability

LangSmith is the **primary observability layer** for this service. Because the
pipeline is a LangGraph compiled graph with LLM calls at every tier, LangSmith
provides end-to-end trace visibility that OTel alone cannot — including per-LLM-call
token counts, prompt/response inspection, confidence score history, and full replay
of any migration run.

### Why LangSmith here specifically

OTel spans (already in every node) capture timing, record counts, and numeric
metrics. LangSmith captures everything the LLM actually did — the exact prompt
sent, the exact response returned, token usage per call, latency per call, and
confidence scores. For a migration pipeline where every mapping decision needs to
be auditable and debuggable, LangSmith is essential. It answers questions OTel
cannot: "Why did Strategy 4 map `EQUIP_CLASS` to `category` with 0.87 confidence
instead of `asset_type`?" — you can open the trace and read the exact prompt and
response.

### Setup

```python
# config.py
LANGSMITH_API_KEY:    str   # from smith.langchain.com
LANGSMITH_PROJECT:    str = "cafm-ai-schema-mapper"
LANGSMITH_ENDPOINT:   str = "https://api.smith.langchain.com"
LANGSMITH_TRACING:    bool = True   # set False to disable in dev
```

```python
# app.py — set before any LangChain/LangGraph import
import os
os.environ["LANGCHAIN_TRACING_V2"]  = "true"
os.environ["LANGCHAIN_API_KEY"]     = settings.LANGSMITH_API_KEY
os.environ["LANGCHAIN_PROJECT"]     = settings.LANGSMITH_PROJECT
os.environ["LANGCHAIN_ENDPOINT"]    = settings.LANGSMITH_ENDPOINT
```

Setting these four environment variables is sufficient — LangGraph automatically
traces every node execution and every LLM call within nodes to LangSmith without
any additional instrumentation code.

### What LangSmith captures automatically (zero extra code)

Once the env vars are set, LangSmith receives a trace for every migration run
containing:

- **Full graph execution trace** — every node as a span, showing entry/exit time,
  input state, output state, and which conditional edge was taken
- **Every LLM call** — exact system prompt, exact user prompt, full response text,
  model used, token counts (prompt + completion), latency, and any errors
- **LangGraph interrupt events** — when and where the graph paused, what the
  interrupt payload contained, when it resumed and with what
- **Conditional edge decisions** — which branch was taken and why (state values
  that drove the condition)
- **Error traces** — full stack trace for any node failure, with the state at the
  point of failure

### Run naming convention

Tag every migration run with `migration_id` so traces are findable by job:

```python
# In worker.py — wrap every run with a named trace
from langsmith import traceable

config = {
    "configurable": {"thread_id": str(migration_id)},
    "run_name": f"migration:{migration_id}",
    "tags": [
        f"cmms:{cmms_name}",
        f"org:{organization_id}",
        "svc-ai-schema-mapper",
    ],
    "metadata": {
        "migration_id":    str(migration_id),
        "cmms_name":       cmms_name,
        "organization_id": str(organization_id),
        "source_filename": source_filename,
    },
}

async for event in graph.astream_events(initial_state, config=config, version="v2"):
    ...
```

All traces for a migration are then findable in LangSmith by filtering on
`metadata.migration_id`.

### Per-node LangSmith tagging

Each node adds its own metadata to the LangSmith run context so individual
node traces are filterable by node name, tier, and outcome:

```python
# Pattern used in every node — adds node-level metadata to the active trace
from langchain_core.tracers.context import tracing_v2_enabled

async def deterministic_map(state: MigrationState) -> MigrationState:
    with tracing_v2_enabled(
        project_name=settings.LANGSMITH_PROJECT,
        tags=["tier1", "deterministic_map"],
        metadata={
            "migration_id": str(state["migration_id"]),
            "cmms_name":    state["cmms_name"],
            "field_count":  len(state["source_fields"]),
        }
    ):
        ...
```

### LangSmith datasets for evaluation

Pre-built evaluation datasets are maintained in LangSmith for regression testing:

| Dataset | Contents | Purpose |
|---------|----------|---------|
| `fiix-50-field-mapping` | 50 Fiix CSV columns with ground-truth target mappings | Tier 1 regression — target: ≥ 80% T1 resolution |
| `maximo-60-field-mapping` | 60 Maximo columns with ground-truth mappings | Tier 1 + Tier 2 regression |
| `hierarchy-detection-cases` | 20 schema samples with known FK relationships | Hierarchy node regression |
| `multi-merge-cases` | 15 cases where multi-column merge is required | T2 multi-merge detection |
| `simple-rules-enrichment` | 10 description-only rule docs + expected enriched output | Adapter regression |

Run evals against any prompt change before deploying:

```python
from langsmith import Client
client = Client()
client.run_on_dataset(
    dataset_name="fiix-50-field-mapping",
    llm_or_chain_factory=deterministic_mapper_factory,
    evaluation=["qa"],
    project_name="cafm-mapper-eval",
)
```

### LangSmith feedback loop → prompt refinement

When a human reviewer corrects a mapping in the review queue (Node 4), the
correction is written back to LangSmith as negative feedback on the trace that
produced the wrong mapping:

```python
# In review_queue/corrections_log.py — after human correction committed
from langsmith import Client

client = Client()
client.create_feedback(
    run_id=mapping.langsmith_run_id,    # stored in migration_field_mappings
    key="mapping_correct",
    score=0,                            # 0 = wrong, 1 = correct
    comment=f"Human corrected: {mapping.source_field} → {correction.target_field} "
            f"(was: {mapping.target_field})",
)
```

This builds a feedback dataset in LangSmith over time. Weekly, the prompt
refinement loop (equivalent to Task 3.5 in svc-ingestion) queries LangSmith for
runs with `mapping_correct=0` and uses the correction patterns to suggest
improvements to Strategy 4 and Tier 2 prompts.

### Database addition

Add `langsmith_run_id VARCHAR(100) NULL` to `migration_field_mappings` — stores
the LangSmith trace run ID for every LLM-produced mapping so corrections can be
linked back to the exact trace.

---

## 3. Full Pipeline Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     svc-AI-Schema-Mapper LangGraph                          │
│                                                                             │
│  ┌──────────────┐                                                           │
│  │   Customer   │─── POST /api/migration/start ──────────────────────────► │
│  │   Upload     │    {file, cmms_name, mapping_doc_id}                      │
│  └──────────────┘                                                           │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  NODE 1: ingest_and_configure                                        │   │
│  │  • Auto-detect delimiter, encoding, header row                       │   │
│  │  • Compute table health metrics                                      │   │
│  │  • Load CMMS mapping doc into graph state via RAG                    │   │
│  │  • Preprocessing: dedup, null %, data type inference                 │   │
│  │  EMITS: "Loaded 4,891 records · 47 columns · 0.3% nulls"            │   │
│  └────────────────────────────┬────────────────────────────────────────┘   │
│                               │                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  NODE 2: deterministic_map  (Tier 1)                                 │   │
│  │  Strategy 1: Exact field name match          → confidence 0.99      │   │
│  │  Strategy 2: Alias table from mapping doc    → confidence 0.95–0.98 │   │
│  │  Strategy 3: Regex CMMS naming patterns      → confidence 0.90–0.94 │   │
│  │  Strategy 4: Constrained Haiku call          → confidence 0.85–0.92 │   │
│  │  Gate: fields < 0.85 → unresolved list                              │   │
│  │  EMITS: "43 of 47 fields mapped · 4 forwarded to semantic tier"     │   │
│  └────────────────────────────┬────────────────────────────────────────┘   │
│                               │                                             │
│                    ┌──────────┴──────────┐                                  │
│                    │ all resolved?        │                                  │
│                    │ skip Tier 2 ──YES──►│──────────────────────────────┐  │
│                    └──────────┬──────────┘                              │  │
│                               │ NO                                      │  │
│  ┌─────────────────────────────────────────────────────────────────┐   │  │
│  │  NODE 3: semantic_map  (Tier 2)                                   │   │  │
│  │  • Embed unresolved source fields (voyage-3 / text-embedding-3)  │   │  │
│  │  • Cosine similarity vs pre-cached target schema embeddings       │   │  │
│  │  • > 0.85: auto-accept                                            │   │  │
│  │  • 0.65–0.85: flag for human review + top-3 suggestions          │   │  │
│  │  • < 0.65: mark unmappable                                        │   │  │
│  │  EMITS: "2 fields auto-accepted · 1 flagged · 1 unmappable"      │   │  │
│  └─────────────────────────────────────────────────────────────────┘   │  │
│                               │                                         │  │
│                    ┌──────────┴──────────┐                              │  │
│                    │ review items exist?  │                              │  │
│                    │ NO ─────────────────►│─────────────────────────┐  │  │
│                    └──────────┬──────────┘                          │  │  │
│                               │ YES                                 │  │  │
│  ┌─────────────────────────────────────────────────────────────────┐│  │  │
│  │  NODE 4: human_review  ⏸ interrupt()                             ││  │  │
│  │  • Per-field cards: source field, top-3 suggestions, rationale   ││  │  │
│  │  • Accept / Reject / Manual remap UI                             ││  │  │
│  │  • Graph paused — Postgres checkpointer saves state              ││  │  │
│  │  • Customer can close browser and resume later                   ││  │  │
│  │  EMITS: "Waiting for approval on 1 field"                        ││  │  │
│  └─────────────────────────────────────────────────────────────────┘│  │  │
│                               │ Command(resume=approved)             │  │  │
│                               ▼                                      │  │  │
│  ┌──────────────────────────────────────────────────────────────────┴──┴──┘│
│  │  NODE 5: preprocess_and_validate                                        │
│  │  • Duplicate row detection and removal                                  │
│  │  • Null value handling (fill strategy per field type)                   │
│  │  • Data type coercion (string dates → ISO 8601, numeric cleanup)        │
│  │  • JSON Schema validation against plenum_cafm target spec               │
│  │  • FK integrity pre-check: do referenced IDs exist?                     │
│  │  EMITS: "4,887 records valid · 4 duplicates removed · 0 FK violations" │
│  └────────────────────────────┬──────────────────────────────────────────-─┘
│                               │                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  NODE 6: resolve_hierarchy                                           │   │
│  │  • Heuristic FK scan: column name patterns (_id, _num, _ref, _no)   │   │
│  │  • Implicit code hierarchy detection (SAP PLANT-LINE-UNIT format)   │   │
│  │  • FK data validation: match rate > 80% confirms relationship        │   │
│  │  • LLM classifies: CONTAINMENT / REFERENCE / OWNERSHIP / PART_OF   │   │
│  │  • Cycle detection (DFS) — circular refs flagged immediately        │   │
│  │  • Self-referencing tree resolution (sub-assets)                    │   │
│  │  • Orphan record detection (assets with no location, etc.)          │   │
│  │  EMITS: "Hierarchy: site → location → asset → work_order"          │   │
│  └────────────────────────────┬────────────────────────────────────────┘   │
│                               │                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  NODE 7: verify_hierarchy  ⏸ interrupt()                            │   │
│  │  • Show customer the detected hierarchy visually                    │   │
│  │  • Show all detected relationships with LLM rationale               │   │
│  │  • Highlight orphans and cycle warnings                             │   │
│  │  • Accept / Correct / Add missing relationships                     │   │
│  │  EMITS: "Hierarchy confirmed by customer"                           │   │
│  └────────────────────────────┬────────────────────────────────────────┘   │
│                               │                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  NODE 8: generate_output                                            │   │
│  │  • Produce nested JSON: sites > locations > assets > WOs > tasks   │   │
│  │  • Export: JSON (primary) + CSV (flat) + SQL insert statements      │   │
│  │  • Build IntermediateSchema → hand to svc-ingestion                 │   │
│  │  • Generate PDF migration summary report                            │   │
│  │  • Upload to Azure Blob → return S3-signed URLs                     │   │
│  │  EMITS: "Export complete · 4,887 records · 3 export formats"       │   │
│  └────────────────────────────┬────────────────────────────────────────┘   │
│                               │                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  NODE 9: write_to_platform  ⏸ interrupt() — final approval gate    │   │
│  │  • Show customer final summary before any DB write                  │   │
│  │  • Record counts per object type                                    │   │
│  │  • Mapping coverage stats (% resolved per tier)                     │   │
│  │  • Customer confirms → IntermediateSchema → svc-ingestion pipeline  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  Postgres checkpointer active throughout — every state transition saved    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. LangGraph State Definition

```python
# svc-ai-schema-mapper/src/graph/state.py

from typing import TypedDict, Literal, Any
from uuid import UUID
from datetime import datetime


class FieldMapping(TypedDict):
    source_field: str                # primary source column name
    source_fields: list[str] | None  # multi-column merge: ["first_name", "last_name"] → "Name"
    merge_strategy: str | None       # "concat_space" | "concat_comma" | "coalesce" | None
    target_field: str
    confidence: float
    tier: Literal["T1_exact", "T1_alias", "T1_regex", "T1_llm",
                  "T2_semantic", "T2_human", "T2_multi_merge", "unmapped"]
    rationale: str
    sample_values: list[Any]
    transformation: str | None   # e.g. "string_to_uuid", "date_iso"


class HierarchyRelationship(TypedDict):
    source_table: str
    source_column: str
    target_table: str
    relationship_type: Literal["CONTAINMENT", "REFERENCE", "OWNERSHIP",
                                "PART_OF", "SELF_REF"]
    direction: str               # human-readable e.g. "asset belongs to location"
    confidence: float
    data_match_rate: float       # FK validation result
    reasoning: str               # shown to customer


class TableHealth(TypedDict):
    table_name: str
    row_count: int
    column_count: int
    null_pct: float
    duplicate_count: int
    data_types: dict[str, str]   # col_name → inferred type


class MigrationState(TypedDict):
    # ── Job identity ──────────────────────────────────────────────────────
    migration_id: UUID
    cmms_name: str               # "IBM Maximo" | "SAP PM" | "Fiix" | etc.
    organization_id: UUID
    started_at: datetime

    # ── Raw input ─────────────────────────────────────────────────────────
    source_file_bytes: bytes
    source_filename: str
    mapping_doc_content: str     # loaded from RAG / vector store

    # ── NODE 1 outputs ────────────────────────────────────────────────────
    parsed_tables: dict[str, list[dict]]   # table_name → rows
    table_health: list[TableHealth]
    source_fields: list[str]              # all columns detected
    detected_encoding: str
    detected_delimiter: str
    column_descriptions: dict[str, str]   # LLM-generated per-column semantic descriptions
    dataset_summary: str                  # prose summary of the overall dataset

    # ── NODE 2 outputs (Tier 1) ───────────────────────────────────────────
    tier1_mappings: list[FieldMapping]
    unresolved_after_t1: list[str]        # fields that fell below 0.85

    # ── NODE 3 outputs (Tier 2) ───────────────────────────────────────────
    tier2_auto_accepted: list[FieldMapping]
    tier2_flagged_for_review: list[FieldMapping]   # 0.65–0.85
    tier2_unmappable: list[str]           # below 0.65
    tier2_top3_suggestions: dict[str, list[FieldMapping]]  # field → 3 options

    # ── NODE 4 outputs (human review) ────────────────────────────────────
    human_approved_mappings: list[FieldMapping]
    human_rejected_fields: list[str]
    all_resolved_mappings: list[FieldMapping]   # T1 + T2 + human combined

    # ── NODE 5 outputs (preprocessing + validation) ───────────────────────
    clean_tables: dict[str, list[dict]]   # deduplicated, typed, validated
    validation_errors: list[str]
    preprocessing_summary: dict           # duplicates removed, nulls filled, etc.

    # ── NODE 6 outputs (hierarchy) ────────────────────────────────────────
    fk_candidates: list[dict]
    validated_relationships: list[HierarchyRelationship]
    containment_hierarchy: list[str]      # ["site → location → asset → work_order"]
    root_objects: list[str]
    orphaned_records: dict[str, list]     # table → list of orphan rows
    hierarchy_cycles: list[str]           # empty if clean
    resolved_trees: dict                  # sub-asset trees

    # ── NODE 7 outputs (hierarchy review) ────────────────────────────────
    hierarchy_confirmed: bool
    hierarchy_corrections: list[dict]     # customer overrides

    # ── NODE 8 outputs (export) ───────────────────────────────────────────
    output_json: dict                     # nested hierarchy JSON
    output_csv_url: str
    output_json_url: str
    output_sql_url: str
    migration_report_url: str             # PDF summary
    intermediate_schema: dict             # → svc-ingestion contract

    # ── Progress tracking (streamed to UI) ───────────────────────────────
    current_step: str
    completed_steps: list[str]
    step_summaries: dict[str, str]        # step_name → customer-readable summary
    overall_progress_pct: float
    requires_human_review: bool
    migration_status: Literal["running", "awaiting_review", "complete",
                               "failed", "cancelled"]
```

---

## 5. Node Responsibilities

### Node 1 — `ingest_and_configure`

**File:** `graph/nodes/ingest_node.py`

Reads the uploaded file and prepares the graph state for the mapping pipeline.

- Auto-detects encoding using `chardet` — supports UTF-8, latin-1, and common CMMS export encodings
- Auto-detects delimiter by frequency counting across `,` `;` `\t` `|` `:` on a 4KB sample
- Detects whether the first row is a true header or data (all-numeric column names → treat as headerless)
- Parses up to 500,000 rows via pandas, capped to prevent memory issues
- Computes table health metrics: row count, column count, null percentage per column, duplicate count, inferred semantic type per column (numeric / date_iso / date_dmy / email / code_ref / text)
- **Dataset description via LLM** (`describe_dataset` sub-step): sends the first 5 rows (`df.head()`) to Haiku with a data-analysis system prompt. Haiku returns a per-column semantic description that goes beyond column names — e.g. `"PM-2024-001"` values described as *"Preventive maintenance work order reference codes with year prefix"*. These descriptions are stored in `column_descriptions: dict[str, str]` in graph state and used by both Strategy 4 (Tier 1) and the semantic mapper (Tier 2) to dramatically improve matching accuracy on cryptic column names
- Loads the CMMS-specific mapping key document from `plenum_cafm.document_chunks` (full-text search first; vector similarity when voyage-3 is active) and writes it into graph state
- Emits customer-facing summary: `"Loaded 4,891 records · 47 columns · 0.3% nulls · 12 duplicates detected"`
- OTel span: `migration.ingest` with `cafm.encoding`, `cafm.delimiter`, `cafm.row_count`, `cafm.null_pct`, `cafm.duplicate_count`
- **LangSmith**: `describe_dataset()` Haiku call is traced automatically. The trace captures the exact `df.head()` string sent, full JSON response, token count, and latency. Tagged `["node1", "dataset_description", "ingest_and_configure"]`. If column descriptions are wrong or too generic, this trace is the first place to debug

```python
# Dataset description — sub-step of Node 1
def describe_dataset(self, df_head: pd.DataFrame) -> dict[str, str]:
    """Send first 5 rows to Haiku for per-column semantic descriptions."""
    system_prompt = """You are a data analysis expert specialising in CMMS and
    facility management datasets. When given a DataFrame's head output, analyze
    each column and return a JSON object mapping column_name → description.
    Focus on: what the column represents in a CMMS context, the data pattern
    (codes, dates, free text, numeric IDs), and any naming convention clues."""

    response = self.llm.invoke(
        system_prompt=system_prompt,
        user_prompt=f"Analyze this DataFrame:\n{df_head.to_string()}",
        temperature=0.3,
        max_tokens=2000,
    )
    return json.loads(response)  # {"ASSETNUM": "Unique asset identifier...", ...}
```

---

### Node 2 — `deterministic_map` (Tier 1)

**File:** `graph/nodes/deterministic_mapper.py` — `DeterministicMatcher` class

**Target schema** (29 canonical fields from CLAUDE.md §7): `asset_code`, `asset_name`, `category`, `location_code`, `make`, `model`, `serial`, `wo_code`, `wo_priority`, `wo_status`, `wo_type`, `maintenance_type`, `sm_code`, `trigger_type`, `schedule_interval`, `sm_priority`, `part_code`, `stock_on_hand`, `minimum_allowed_stock`, `supplier`, `bom_group_name`, `user_full_name`, `user_title`, `user_name`, `reports_to`, `inspector_name`, `inspection_date`, `inspection_location`, `finding_type`, `risk_level`

# CMMS-specific alias tables (add new platforms without code changes)
CMMS_ALIASES = {

**4 strategies run in cascade — first match above 0.85 wins:**

- **Strategy 1 — Exact match** (confidence 0.99): source field name lowercased matches a canonical target field name directly
- **Strategy 2 — Alias table lookup** (confidence 0.95–0.98): source field found in the CMMS-specific alias dict (e.g. Maximo's `assetnum` → `asset_code`, SAP's `equnr` → `asset_code`, Fiix's `strCode` → `asset_code`). Alias dicts are maintained in `matchers/cmms_aliases.py` — adding a new CMMS requires no code changes to the node itself
- **Strategy 2b — Mapping doc rules** (confidence 0.90–0.98): source field matched against aliases and patterns extracted from the customer-uploaded mapping key document loaded in Node 1. The mapping doc is parsed as JSON if structured, or Haiku extracts the rules if unstructured
- **Strategy 3 — Regex pattern rules** (confidence 0.90–0.94): source field matched against 16 common CMMS naming convention patterns (e.g. `.*_id$` → `asset_code`, `wo.*num$` → `wo_code`, `inspection.*date` → `inspection_date`). Patterns maintained in `matchers/regex_patterns.py`
- **Strategy 4 — Constrained Haiku call** (confidence 0.85–0.92): if no rule matched, a single Haiku call with the full target schema, mapping doc context, and 5 sample values from the column. Returns structured JSON with `target_field`, `confidence`, `rationale`, and `transformation`. Fields returned below 0.85 are forwarded to `unresolved_after_t1`

Every matched field produces a `FieldMapping` with `source_field`, `target_field`, `confidence`, `tier`, `rationale`, `sample_values`, and optional `transformation` (e.g. `date_iso`, `numeric_invert_to_enum`). Every match is written to `migration_field_mappings` audit table.

- OTel span: `migration.deterministic_map` with `cafm.t1_matched`, `cafm.t1_unresolved`, `cafm.t1_llm_calls`
- **LangSmith**: every Strategy 4 Haiku call is traced individually. Each trace shows the exact prompt (target schema + mapping doc context + sample values), response JSON, confidence score returned, and whether the field was accepted or forwarded to Tier 2. Run tag `["node2", "tier1", "strategy4"]`. Filterable by `metadata.source_field` to debug specific field mismatches. The `langsmith_run_id` from each Strategy 4 call is stored in `migration_field_mappings.langsmith_run_id` for correction feedback linkage

---

### Node 3 — `semantic_map` (Tier 2)

**File:** `graph/nodes/semantic_mapper.py`

Only runs if `unresolved_after_t1` is non-empty — skipped via conditional edge if Tier 1 resolved everything.

- At service startup, all 29 target schema field names + their descriptions are embedded once using voyage-3 (or `text-embedding-3-small` as fallback) and cached in memory — never recomputed per migration
- For each unresolved source field: embeds the field name + 3 sample values as a single query string, then computes cosine similarity against all 29 cached target embeddings
- Ranked results are bucketed by threshold:
  - Score ≥ 0.85 → auto-accepted, added to `tier2_auto_accepted`
  - Score 0.65–0.85 → flagged for human review, added to `tier2_flagged_for_review` with top-3 alternatives
  - Score < 0.65 → marked unmappable, added to `tier2_unmappable`
- Sample values are the single most important context signal — `["PM-2024-001", "PM-2024-002"]` tells the embedder this is a work order reference even if the column name is cryptic
- **Column descriptions from Node 1** are appended to the embedding query alongside sample values — e.g. query becomes `"PLUSPCUSTOMER | Represents the customer reference code assigned to Plus-P extended assets | ['CUST-001', 'CUST-002']"`. This dramatically improves similarity scores on cryptic field names
- Top-3 alternatives per flagged field are stored in `tier2_top3_suggestions` and surfaced in the review UI so the customer can pick from a ranked list rather than typing free-form

**1:N column compatibility checking** — after standard embedding similarity, a second pass runs the `check_column_compatibility` strategy. For each _unmapped target field_, the system checks all remaining source columns for compatibility using an LLM call with the target field description and the source column's LLM-generated description from Node 1. This catches cases where column names are completely unrelated but the data is compatible (e.g. source column `"Remarks"` maps to target `"Info"`):

```python
# 1:N compatibility check — runs after embedding similarity
def check_column_compatibility(self, target_name: str, target_desc: str,
                                source_columns: dict[str, str]) -> str | None:
    """Check one target field against ALL remaining source columns.
    source_columns: {col_name: llm_description} from Node 1 describe_dataset."""
    system_prompt = """You are an expert in checking compatibility for mapping
    columns across different datasets. Given a target column and its description,
    determine the best match from the source columns. Return the source column
    name, or 'Nothing Compatible' if no match exists."""

    response = self.llm.invoke(
        user_prompt=f"Target: {target_name}\nDescription: {target_desc}\n"
                    f"Source columns:\n{json.dumps(source_columns, indent=2)}",
        system_prompt=system_prompt,
        max_tokens=500,
    )
    result = response.strip()
    return None if result == "Nothing Compatible" else result
```

**Multi-column merge detection** — for target fields that represent composite data (e.g. `user_full_name` = first + last name, `location_code` = building + floor + room), the semantic mapper detects when no single source column matches but a _combination_ of source columns would. Haiku is prompted with the target description + all source column descriptions and asked to identify merge candidates:

```python
# Multi-column merge — detects N:1 source→target mappings
def detect_multi_column_merge(self, target_name: str, target_desc: str,
                               source_columns: dict[str, str]) -> FieldMapping | None:
    """Detect if multiple source columns should be merged into one target."""
    prompt = f"""Target field: {target_name}
    Description: {target_desc}
    Source columns: {json.dumps(source_columns)}

    Should multiple source columns be COMBINED to produce this target field?
    If yes, return JSON: {{"source_fields": ["col1", "col2"], "merge_strategy": "concat_space", "confidence": 0.XX}}
    If no single or multi-column match exists, return {{"source_fields": [], "confidence": 0.0}}"""

    result = self.llm.invoke(prompt)
    parsed = json.loads(result)
    if parsed["source_fields"] and parsed["confidence"] >= 0.70:
        return FieldMapping(
            source_field=parsed["source_fields"][0],
            source_fields=parsed["source_fields"],
            merge_strategy=parsed.get("merge_strategy", "concat_space"),
            target_field=target_name,
            confidence=parsed["confidence"],
            tier="T2_multi_merge",
            rationale=f"Multi-column merge: {' + '.join(parsed['source_fields'])} → {target_name}",
            sample_values=[],
            transformation="multi_merge",
        )
    return None
```

Merge strategies supported: `concat_space` (join with space), `concat_comma` (join with comma), `coalesce` (first non-null value), `concat_dash` (join with hyphen). The export layer (`json_builder.py` and `csv_exporter.py`) applies the merge at export time using:

```python
# Applied in generate_output node during export
if mapping.source_fields and mapping.merge_strategy:
    cols = [df[c].fillna('').astype(str) for c in mapping.source_fields]
    sep = {"concat_space": " ", "concat_comma": ", ", "concat_dash": "-"}.get(mapping.merge_strategy, " ")
    if mapping.merge_strategy == "coalesce":
        new_df[mapping.target_field] = df[mapping.source_fields].bfill(axis=1).iloc[:, 0]
    else:
        new_df[mapping.target_field] = cols[0].str.cat(cols[1:], sep=sep)
```

- OTel span: `migration.semantic_map` with `cafm.t2_auto_accepted`, `cafm.t2_flagged`, `cafm.t2_unmappable`, `cafm.t2_multi_merge`, `cafm.t2_compatibility_checks`
- **LangSmith**: three separate LLM call types are traced in this node, each tagged distinctly:
  - `check_column_compatibility()` calls → tagged `["node3", "tier2", "compatibility_check"]`. Each trace shows target field + source column descriptions sent, response (matched column name or "Nothing Compatible"), token cost per check
  - `detect_multi_column_merge()` calls → tagged `["node3", "tier2", "multi_merge_detection"]`. Shows the merge candidate prompt, response with `source_fields` + `merge_strategy`, and confidence. `langsmith_run_id` stored in `migration_field_mappings.langsmith_run_id` for any resulting `T2_multi_merge` mapping
  - Embedding calls are not LLM calls and are not traced by LangSmith — these are captured by OTel only

---

### Node 4 — `human_review` ⏸ interrupt gate

**File:** `graph/nodes/human_review_node.py`

Only runs if `tier2_flagged_for_review` is non-empty — skipped via conditional edge otherwise.

- Calls LangGraph `interrupt()` with a structured review payload: per-field cards containing the source field name, suggested target mapping, confidence score, LLM rationale, sample values, and top-3 alternatives
- Graph pauses here — the Postgres checkpointer saves the complete `MigrationState` to DB. Customer can close the browser and return later; the graph resumes exactly from this point
- Frontend shows approval queue UI: one card per flagged field with Accept / Reject / Manual remap buttons and the LLM's rationale displayed
- Graph resumes when `POST /api/migration/{id}/approve` is called with `Command(resume=decisions)`. Each decision carries `action` (accept/reject), `target_field`, and optional `notes`
- Accepted fields become `FieldMapping` objects with `tier="T2_human"`. Rejected fields are logged as unmappable. All resolved mappings (T1 + T2 auto + T2 human) are merged into `all_resolved_mappings`
- OTel span: `migration.human_review` with `cafm.approved_count`, `cafm.rejected_count`
- **LangSmith**: the interrupt event itself is traced as a node span showing the review payload (fields presented for approval), the resume payload (customer decisions), and the time elapsed between interrupt and resume. Tagged `["node4", "human_review", "interrupt"]`. Human corrections submitted here trigger negative feedback written to the LangSmith trace of the original mapping call (via `langsmith_run_id` stored in `migration_field_mappings`)

---

### Node 5 — `preprocess_and_validate`

**File:** `graph/nodes/preprocess_node.py`

Cleans the raw parsed data before hierarchy detection and export.

- **Deduplication**: drops exact-duplicate rows, logs count to `preprocessing_summary`
- **Null handling**: numeric nulls filled with 0; text nulls filled with empty string; date nulls left as-is (never fabricated)
- **Date coercion**: columns inferred as date type in Node 1 are normalised to ISO 8601 (`YYYY-MM-DD`) across 5 supported input formats: `%Y-%m-%d`, `%d/%m/%Y`, `%m/%d/%Y`, `%d %b %Y`, `%d-%b-%Y`. Unparseable values returned as-is and flagged
- **JSON Schema validation**: mapped rows validated against the `plenum_cafm` target schema spec — type mismatches, out-of-range values, and invalid enums collected into `validation_errors` (warnings, not blockers)
- **FK pre-check**: verifies that referenced IDs in the mapped data resolve to existing records within the same dataset — orphans flagged here before hierarchy detection
- Emits: `"4,887 records valid · 4 duplicates removed · 0 FK violations"`
- OTel span: `migration.preprocess` with `cafm.duplicates_removed`, `cafm.nulls_filled`, `cafm.validation_errors`
- **LangSmith**: no LLM calls in this node — OTel is the observability layer here. The node execution span is still captured by LangSmith as part of the overall graph trace, showing input/output state and duration

---

### Node 6 — `resolve_hierarchy`

**File:** `graph/nodes/hierarchy_node.py` + `hierarchy/` submodules

Four-layer process to reconstruct the object hierarchy from flat data.

**Layer 1 — Heuristic FK column scan** (`hierarchy/fk_scanner.py`): scans all column names against 8 FK patterns (`.*_id$`, `.*_num$`, `.*_no$`, `.*_ref$`, `.*_code$`, `parent_.*`, `.*_parent$`, `.*_key$`) and 3 self-reference patterns. Infers target table from the column name stem (e.g. `asset_id` → `asset` table). Initial confidence: 0.70.

**Layer 1b — Implicit code hierarchy detection** (`hierarchy/implicit_hierarchy.py`): detects SAP-style hierarchies encoded in structured code strings (e.g. `PLANT1-LINE2-UNIT3`). Finds a consistent separator across > 80% of values in a column, builds a parent-child map from code prefixes. Confidence: 0.80.

**Layer 2 — FK data validation** (`hierarchy/fk_validator.py`): for each FK candidate, samples up to 500 values from the source column and checks how many appear in the target table's primary key column. Match rate ≥ 0.80 confirms the FK; below that, the candidate is discarded. Updates confidence to average of heuristic confidence and data match rate.

**Layer 3 — LLM relationship classification**: sends validated FK candidates + 3 sample rows per table to Haiku. Classifies each relationship as `CONTAINMENT` (parent physically contains child), `REFERENCE` (source references target without ownership), `OWNERSHIP` (source owns target lifecycle), or `PART_OF` (component belongs to BOM). Returns `containment_hierarchy` list and `root_objects` list with reasoning per relationship.

**Layer 4 — Cycle detection** (`hierarchy/cycle_detector.py`): DFS traversal of the containment graph. Any cycle (e.g. asset → location → asset) is immediately flagged as a data quality issue and surfaced to the customer — never silently accepted.

**Layer 5 — Self-referencing tree resolution** (`hierarchy/tree_resolver.py`): for tables with a self-referencing FK (e.g. `asset.parent_asset_id → asset.id`), recursively builds nested trees. Root assets (no parent) become tree roots; children nested under their parent's `children` list.

**Orphan detection**: records with FK values pointing to non-existent parents are collected into `orphaned_records` — surfaced to the customer as data quality findings.

- OTel span: `migration.hierarchy` with `cafm.relationships_found`, `cafm.cycles_found`, `cafm.orphans_found`
- **LangSmith**: the LLM relationship classification call (Layer 3) is traced with tag `["node6", "hierarchy", "relationship_classification"]`. The trace shows: all validated FK candidates sent as context, the 3 sample rows per table, the full LLM response with relationship types and reasoning, and the final `containment_hierarchy` string. This is the most useful trace for debugging incorrect hierarchy detection — e.g. why a `REFERENCE` was classified as `CONTAINMENT`

---

### Node 7 — `verify_hierarchy` ⏸ interrupt gate

**File:** `graph/nodes/verify_hierarchy_node.py`

Pauses the graph and presents the detected hierarchy to the customer for confirmation before any export happens.

- Calls `interrupt()` with the full relationship graph, containment hierarchy string, orphan list, and cycle warnings
- Frontend renders the hierarchy visually: a tree showing `site → location → asset → work_order → task` with each relationship's confidence and LLM rationale
- Customer can accept the detected hierarchy, correct individual relationships (change `REFERENCE` to `CONTAINMENT`, add a missing link), or flag orphans for manual handling
- Corrections written back into `validated_relationships` and `hierarchy_corrections` before graph resumes
- OTel span: `migration.verify_hierarchy` with `cafm.corrections_made`
- **LangSmith**: interrupt/resume event traced as a node span showing the hierarchy payload presented to the customer, any corrections they submitted, and time to approval. Tagged `["node7", "verify_hierarchy", "interrupt"]`

---

### Node 8 — `generate_output`

**File:** `graph/nodes/output_generator.py` + `export/` submodules

Produces all output artefacts from the clean, hierarchy-resolved data.

- **Nested JSON** (`export/json_builder.py`): traverses the confirmed containment hierarchy and nests objects accordingly — `sites > locations > assets > work_orders > tasks`. Self-referencing trees (sub-assets) are nested at arbitrary depth. REFERENCE relationships become ID links rather than nested objects
- **Flat CSV** (`export/csv_exporter.py`): all tables exported as flat CSV with canonical column names — for customers who need tabular output for further processing
- **SQL INSERT statements** (`export/sql_exporter.py`): generates parameterised `INSERT INTO plenum_cafm.<table>` statements in FK-dependency order (sites first, then locations, then assets, etc.) — ready to run directly against the target DB
- **IntermediateSchema**: builds the standard `IntermediateSchema` Pydantic model (defined in `svc-ingestion/src/shared/intermediate_schema.py`) from the output JSON — this is the handoff to `svc-ingestion`
- **PDF migration summary report** (`export/report_generator.py`): auto-generated PDF covering tier breakdown (T1/T2/human resolved counts), mapping coverage per table, confidence distribution histogram, orphan list, hierarchy diagram, and full decision log. Built with `reportlab`
- **Mapping flow document** (`export/mapping_flow_doc.py`): auto-generated PDF/Word document that captures the complete hierarchical processing and mapping flow as a permanent record of the migration. This document is part of the official data migration deliverables and includes:
  - **Section 1 — Source analysis**: dataset summary from Node 1, table health metrics, column descriptions from the LLM analysis, data quality findings (null rates, duplicate counts)
  - **Section 2 — Field mapping decisions**: every source-to-target mapping with tier (T1/T2/human), confidence score, match strategy used, rationale, and sample values. Organised by source table with a visual mapping diagram showing source fields on the left → target fields on the right with colour-coded confidence
  - **Section 3 — Multi-column merges**: any N:1 column merges with the merge strategy, source columns, target field, and sample output
  - **Section 4 — Hierarchy resolution**: the detected containment tree rendered as a visual diagram, all relationship types (CONTAINMENT, REFERENCE, OWNERSHIP, PART_OF), FK validation results with match rates, cycle detection results, orphan records, and customer corrections
  - **Section 5 — Processing steps timeline**: chronological log of every pipeline step with timestamps, duration, records processed, and customer approval decisions — mirrors the step sequencer UI
  - **Section 6 — Validation results**: JSON Schema validation summary, FK integrity check results, data type coercion log, records accepted/rejected/modified
  - **Appendix**: full mapping lookup table (source field → target field → confidence → tier) and raw audit trail export
  - Generated in both PDF (primary) and DOCX (for customer editing) formats using `reportlab` and `python-docx`
  - Uploaded alongside other artefacts to Azure Blob under `migrations/{migration_id}/mapping_flow_report.pdf`
- All artefacts uploaded to Azure Blob under `migrations/{migration_id}/` and S3-signed URLs returned
- OTel span: `migration.generate_output` with `cafm.records_exported`, `cafm.export_formats`
- **LangSmith**: no LLM calls in this node — export is deterministic. Node execution span is captured as part of the graph trace showing input state (clean tables + relationships) and output state (all export URLs). Duration tracked for performance monitoring

---

### Node 9 — `write_to_platform` ⏸ final approval gate

**File:** `graph/nodes/write_node.py`

Final interrupt before any data enters the CAFM platform DB.

- Presents the customer with a summary: record counts per object type, mapping coverage %, export file links, and migration report PDF link
- Customer reviews and confirms — graph resumes
- On confirmation: `IntermediateSchema` is posted to `svc-ingestion` via internal HTTP call, which runs the standard EL-2.2 → EL-2.3 → Stage 4 unifier → `plenum_cafm` write pipeline
- `migration_jobs` record updated to `status = complete` with `completed_at` timestamp
- OTel span: `migration.write_to_platform` with `cafm.entities_submitted`
- **LangSmith**: interrupt/resume traced as a node span. The final graph trace for the migration is closed here — the complete end-to-end run from Node 1 to Node 9 is visible as a single trace tree in LangSmith, showing every LLM call, every node transition, all interrupt/resume events, and total token cost for the migration. Tagged `["node9", "write_to_platform", "interrupt"]`

---

## 6. Graph Wiring

**File:** `graph/migration_graph.py`

The `StateGraph` is compiled once at service startup with a `PostgresSaver` checkpointer, making every run resumable.

**Node registration:** all 9 nodes registered with `workflow.add_node()`

**Edges:**
- `ingest_and_configure` → `deterministic_map` (always)
- `deterministic_map` → conditional: if `unresolved_after_t1` is empty → `preprocess_and_validate` (skip Tier 2); otherwise → `semantic_map`
- `semantic_map` → conditional: if `tier2_flagged_for_review` is empty → `preprocess_and_validate` (skip review); otherwise → `human_review`
- `human_review` → `preprocess_and_validate` (always, after resume)
- `preprocess_and_validate` → `resolve_hierarchy` → `verify_hierarchy` → `generate_output` → `write_to_platform` → END (all linear)

**Interrupt points** (graph pauses and saves state before entering these nodes):
- `human_review` — field mapping approval
- `verify_hierarchy` — hierarchy confirmation
- `write_to_platform` — final write approval

**Postgres checkpointer:** `PostgresSaver.from_conn_string(DB_URL)` — stores full `MigrationState` at every state transition keyed by `migration_id` as `thread_id`. Supports concurrent migrations from multiple customers without state collision.

**LangSmith tracing on the compiled graph:** once the four env vars are set (see §2a), the compiled graph automatically sends a trace to LangSmith for every `graph.astream_events()` or `graph.ainvoke()` call. Each trace is a tree: the root span is the full migration run, child spans are individual node executions, and grandchild spans are individual LLM calls within nodes. The `run_name`, `tags`, and `metadata` passed in the `config` dict (see §2a run naming) make every migration trace findable and filterable in the LangSmith UI.

---

## 7. FastAPI Endpoints

**File:** `src/app.py` — port 8003

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/migration/start` | Upload CMMS export + start pipeline. Returns `migration_id` immediately; pipeline runs async via ARQ |
| `POST` | `/api/migration/{id}/approve` | Resume graph after any interrupt. Body: list of per-field or per-relationship decisions |
| `GET` | `/api/migration/{id}/status` | Returns `current_step`, `progress_pct`, `completed_steps`, `step_summaries`, `status`, `review_items` |
| `GET` | `/api/migration/{id}/audit` | Full audit trail — all field mapping decisions with tier, confidence, rationale, timestamp |
| `GET` | `/api/migration/{id}/download/{format}` | Returns signed Azure Blob URL for `json`, `csv`, `sql`, `report`, or `mapping_flow` |
| `GET` | `/api/migration/list` | All migrations for the organisation with status and progress |
| `DELETE` | `/api/migration/{id}` | Cancel an in-progress migration (sets status = cancelled) |
| `WS` | `/ws/migration/{id}` | WebSocket — streams node start/complete events to the customer UI in real-time using `graph.astream_events()` |
| `GET` | `/api/migration/{id}/langsmith` | Returns the LangSmith trace URL for a migration run — links directly to the full execution trace in the LangSmith UI for internal debugging |

**Customer-facing step names** (mapped from internal node names for the WebSocket stream):

| Internal node | Customer sees |
|---|---|
| `ingest_and_configure` | Loading and analysing your file |
| `deterministic_map` | Matching fields using mapping rules |
| `semantic_map` | AI semantic field matching |
| `human_review` | Waiting for your confirmation |
| `preprocess_and_validate` | Cleaning and validating data |
| `resolve_hierarchy` | Detecting object relationships |
| `verify_hierarchy` | Confirming hierarchy with you |
| `generate_output` | Generating export files |
| `write_to_platform` | Writing to your CAFM platform |

---

## 8. Predefined Mapping Rules Format

The mapping rules document (per CMMS platform) follows this JSON schema.
New CMMS platforms are added by uploading a new mapping doc — no code changes.

```json
{
  "cmms_name": "IBM Maximo",
  "version": "7.6.1",
  "mapping_rules": {
    "asset_code": {
      "description": "Unique asset identifier in Maximo",
      "source_aliases": ["ASSETNUM", "assetnum", "ASSET_NUM", "asset_id"],
      "regex_patterns": ["^ASS-\\d+", "^MOB-[A-Z]+-\\d+"],
      "data_type": "string",
      "required": true
    },
    "wo_code": {
      "description": "Work order number — primary identifier for maintenance jobs",
      "source_aliases": ["WONUM", "wonum", "WO_NUM", "WORKORDERID"],
      "regex_patterns": ["^WO-\\d+", "^\\d{7}$"],
      "data_type": "string",
      "required": true
    },
    "location_code": {
      "description": "Physical location identifier — maps to LOCATION or SITEID",
      "source_aliases": ["LOCATION", "SITEID", "LOCNUM", "site_id"],
      "regex_patterns": [],
      "data_type": "string",
      "required": false
    },
    "wo_priority": {
      "description": "Work order priority — Maximo uses 1=highest, 5=lowest",
      "source_aliases": ["WOPRIORITY", "PRIORITY", "PRIOK"],
      "transformation": "numeric_invert_to_enum",
      "enum_map": {"1": "Highest", "2": "High", "3": "Medium", "4": "Low", "5": "Lowest"},
      "data_type": "enum",
      "required": false
    }
  },
  "hierarchy": {
    "containment": ["site → location → asset"],
    "references": ["work_order → asset", "work_order → location"],
    "self_ref": ["asset.parent → asset.id"]
  }
}
```

### Simple rules format (description-only)

For quick onboarding of new platforms or when detailed aliases are not yet known,
the system also accepts a **simplified rules format** with description-only fields.
The adapter auto-converts this to the full format at ingest time by using Haiku to
generate probable aliases and regex patterns from the description.

```json
{
  "mapping_rules": {
    "Name": {
      "description": "Represents the full name of a person, which can include first and last name or only one."
    },
    "Contact": {
      "description": "Represents a phone number or any contact number. Does not include email."
    },
    "Email": {
      "description": "Represents the email address. Must be valid email format."
    },
    "Info": {
      "description": "Any additional relevant information, remarks, or general details."
    },
    "Location": {
      "description": "Geographical location — city, state, country, or full address."
    }
  }
}
```

**Adapter implementation** (`matchers/mapping_doc_parser.py`):

```python
def adapt_simple_rules(simple_doc: dict) -> dict:
    """Convert description-only rules to the full mapping doc format.
    Uses Haiku to infer probable source_aliases and regex_patterns
    from each field's description."""

    full_doc = {
        "cmms_name": simple_doc.get("cmms_name", "Unknown"),
        "version": simple_doc.get("version", "1.0"),
        "mapping_rules": {},
        "hierarchy": simple_doc.get("hierarchy", {}),
    }

    for target_field, rule in simple_doc.get("mapping_rules", {}).items():
        if "source_aliases" in rule:
            # Already in full format — pass through
            full_doc["mapping_rules"][target_field] = rule
            continue

        # Simple format: only has "description" — enrich via Haiku
        enriched = llm.invoke(
            system_prompt="Given a target field name and description from a CMMS mapping, "
                          "generate probable source column aliases and regex patterns. "
                          "Return JSON: {source_aliases: [...], regex_patterns: [...], data_type: '...'}",
            user_prompt=f"Field: {target_field}\nDescription: {rule['description']}",
        )
        parsed = json.loads(enriched)
        full_doc["mapping_rules"][target_field] = {
            "description": rule["description"],
            "source_aliases": parsed.get("source_aliases", []),
            "regex_patterns": parsed.get("regex_patterns", []),
            "data_type": parsed.get("data_type", "string"),
            "required": rule.get("required", False),
        }

    return full_doc
```

The adapter runs once during Node 1 (`ingest_and_configure`) after the mapping doc
is loaded. The detection is automatic — if _any_ rule in `mapping_rules` has only a
`description` key and no `source_aliases`, the adapter fires. Mixed formats (some
fields fully specified, others description-only) are handled by passing through
complete fields and enriching simple ones.

**LangSmith tracing for the adapter:** every Haiku enrichment call inside `adapt_simple_rules()` is traced automatically with tag `["mapping_doc_adapter", "simple_rules_enrichment"]`. The trace shows the description passed in and the aliases + patterns generated. If the generated aliases are wrong (e.g. producing irrelevant aliases for a domain-specific field), this trace is where to diagnose and fix the enrichment prompt.

---

## 9. Evaluation Layers

Every node output is evaluated before the graph advances.

| Layer | Node | What is checked | Pass | Fail |
|-------|------|----------------|------|------|
| EL-M.1 | `ingest_and_configure` | File parseable, encoding detected, > 0 rows, > 0 columns | Proceed | Reject + structured error |
| EL-M.2 | `deterministic_map` | All T1 mappings have confidence + tier fields, no duplicate target fields | Proceed | Flag duplicates for T2 |
| EL-M.3 | `semantic_map` | Embeddings computed, cosine scores in 0–1 range, top-3 present | Proceed | Skip field → unmappable |
| EL-M.4 | `human_review` | Resume payload has valid action per flagged field, no unknown fields | Accept decisions | Re-interrupt with error |
| EL-M.5 | `preprocess_and_validate` | Row count post-dedup ≥ 80% of original, no FK violations, schema valid | Proceed | Warn + surface to customer |
| EL-M.6 | `resolve_hierarchy` | No cycles in containment graph, data_match_rate ≥ 0.80 on validated FKs | Proceed | Flag cycles + orphans |
| EL-M.7 | `verify_hierarchy` | Customer confirmed, no unresolved cycles remaining | Proceed | Re-interrupt |
| EL-M.8 | `generate_output` | JSON valid, hierarchy depth ≤ 10, all referenced IDs exist in output | Upload + deliver | Re-validate + surface errors |
| EL-M.9 | `write_to_platform` | IntermediateSchema Pydantic validates, customer confirmed final write | Hand to svc-ingestion | Block + notify |

---

## 10. New Database Tables

```sql
-- Migration job tracking
CREATE TABLE plenum_cafm.migration_jobs (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id       UUID REFERENCES plenum_cafm.organizations(id),
    cmms_name             VARCHAR(100),
    source_filename       VARCHAR(500),
    source_blob_url       TEXT,
    mapping_doc_id        UUID NULL,
    status                VARCHAR(30),   -- running|awaiting_review|complete|failed|cancelled
    current_step          VARCHAR(100),
    progress_pct          NUMERIC(5,2),
    t1_mapped_count       INT,
    t2_auto_count         INT,
    t2_human_count        INT,
    unmapped_count        INT,
    total_fields          INT,
    total_records         INT,
    orphan_count          INT,
    cycle_count           INT,
    output_json_url       TEXT,
    output_csv_url        TEXT,
    output_sql_url        TEXT,
    report_pdf_url        TEXT,
    mapping_flow_url      TEXT,          -- PDF/Word mapping flow document
    t2_multi_merge_count  INT DEFAULT 0, -- multi-column merge mappings
    error_message         TEXT NULL,
    started_at            TIMESTAMPTZ DEFAULT now(),
    completed_at          TIMESTAMPTZ NULL
);

-- Per-field mapping decisions audit (immutable)
CREATE TABLE plenum_cafm.migration_field_mappings (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    migration_id      UUID REFERENCES plenum_cafm.migration_jobs(id),
    source_field      VARCHAR(255),
    source_fields     JSONB NULL,       -- multi-column merge: ["first_name", "last_name"]
    merge_strategy    VARCHAR(50) NULL,  -- concat_space|concat_comma|coalesce|concat_dash
    target_field      VARCHAR(255),
    confidence        NUMERIC(4,3),
    tier              VARCHAR(30),   -- T1_exact|T1_alias|T1_regex|T1_llm|T2_semantic|T2_human|T2_multi_merge|unmapped
    rationale         TEXT,
    sample_values     JSONB,
    transformation    VARCHAR(100) NULL,
    reviewer_id       UUID NULL,     -- set if T2_human
    langsmith_run_id  VARCHAR(100) NULL,  -- LangSmith trace ID for LLM-produced mappings
    decided_at        TIMESTAMPTZ DEFAULT now()
);

-- Hierarchy relationships detected per migration
CREATE TABLE plenum_cafm.migration_hierarchy (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    migration_id          UUID REFERENCES plenum_cafm.migration_jobs(id),
    source_table          VARCHAR(255),
    source_column         VARCHAR(255),
    target_table          VARCHAR(255),
    relationship_type     VARCHAR(50),
    direction             TEXT,
    confidence            NUMERIC(4,3),
    data_match_rate       NUMERIC(4,3),
    reasoning             TEXT,
    customer_confirmed    BOOLEAN DEFAULT false,
    confirmed_at          TIMESTAMPTZ NULL
);
```

---

## 11. Service Scaffold

```
svc-ai-schema-mapper/
├── Dockerfile                        ← same 3-layer pattern as other services
├── pyproject.toml                    ← langgraph, langchain-core, langsmith, anthropic, openai,
│                                        pgvector, pandas, chardet, rapidfuzz
├── src/
│   ├── app.py                        ← FastAPI + lifespan (port 8003) + LangSmith env var setup
│   ├── config.py                     ← Settings — ANTHROPIC_API_KEY, OPENAI_API_KEY,
│   │                                     LANGSMITH_API_KEY, LANGSMITH_PROJECT,
│   │                                     DB_URL, REDIS_URL, AZURE_STORAGE_*
│   ├── worker.py                     ← ARQ worker: run_migration task
│   ├── graph/
│   │   ├── state.py                  ← MigrationState TypedDict
│   │   ├── migration_graph.py        ← StateGraph wiring + checkpointer
│   │   └── nodes/
│   │       ├── ingest_node.py        ← Node 1
│   │       ├── deterministic_mapper.py ← Node 2 + DeterministicMatcher class
│   │       ├── semantic_mapper.py    ← Node 3
│   │       ├── human_review_node.py  ← Node 4 (interrupt)
│   │       ├── preprocess_node.py    ← Node 5
│   │       ├── hierarchy_node.py     ← Node 6
│   │       ├── verify_hierarchy_node.py ← Node 7 (interrupt)
│   │       ├── output_generator.py   ← Node 8
│   │       └── write_node.py         ← Node 9 (interrupt)
│   ├── matchers/
│   │   ├── cmms_aliases.py           ← CMMS_ALIASES dict (Maximo, SAP, Fiix, etc.)
│   │   ├── regex_patterns.py         ← REGEX_PATTERNS list
│   │   ├── mapping_doc_parser.py     ← JSON + unstructured doc parser + simple rules adapter
│   │   ├── dataset_describer.py      ← describe_dataset() — LLM analysis of df.head()
│   │   └── column_compatibility.py   ← 1:N compatibility checker + multi-column merge detector
│   ├── hierarchy/
│   │   ├── fk_scanner.py             ← _scan_fk_candidates()
│   │   ├── fk_validator.py           ← _validate_fk_with_data()
│   │   ├── implicit_hierarchy.py     ← SAP code hierarchy detection
│   │   ├── cycle_detector.py         ← DFS cycle detection
│   │   └── tree_resolver.py          ← _resolve_asset_tree()
│   ├── export/
│   │   ├── json_builder.py           ← nested JSON from flat rows + multi-merge apply
│   │   ├── csv_exporter.py           ← flat CSV
│   │   ├── sql_exporter.py           ← SQL INSERT statements
│   │   ├── report_generator.py       ← PDF migration summary (reportlab)
│   │   └── mapping_flow_doc.py       ← PDF/Word mapping flow document (reportlab + python-docx)
│   └── models/
│       └── migration.py              ← SQLAlchemy ORM for the 3 new tables
├── alembic/
│   └── versions/
│       └── 003_add_migration_tables.py
└── tests/
    ├── test_ingest_node.py           ← malformed CSVs, encoding edge cases, large files
    ├── test_deterministic_mapper.py  ← 50-field Fiix sample, > 80% resolution
    ├── test_semantic_mapper.py       ← cosine thresholds, top-3 correctness
    ├── test_hierarchy_node.py        ← cycle detection, FK validation, SAP implicit
    ├── test_output_generator.py      ← nested JSON structure, S3 URLs
    └── test_e2e_migration.py         ← full pipeline: Fiix CSV in → JSON out
```

---

## 12. Key Design Decisions

**LangGraph over raw orchestration** — the interrupt/resume pattern for human
review gates is native in LangGraph and would require significant custom
infrastructure to replicate. The Postgres checkpointer gives resumable runs
for free.

**Claude Haiku for all LLM steps** — Strategy 4 (constrained mapping call)
and hierarchy classification both use Haiku. These are narrow, structured
tasks with bounded output formats — Haiku is sufficient and ~60× cheaper than
Opus.

**Embeddings: voyage-3 preferred, text-embedding-3-small fallback** — voyage-3
is already wired in `doc_embedder.py` (with NULL embeddings pending API key).
`text-embedding-3-small` (OpenAI) is the fallback, consistent with what the
PDF spec describes. The embedding function is injected as a dependency so
the model can be swapped without changing node code.

**Predefined mapping rules as JSON documents** — stored as documents in
`plenum_cafm.document_chunks` (your existing RAG table). Adding a new CMMS
platform means uploading a new JSON mapping doc — zero code changes.

**Three interrupt points** — `human_review`, `verify_hierarchy`, and
`write_to_platform`. Customer always approves before data enters their DB.

**Output feeds `svc-ingestion` unchanged** — the final node produces an
`IntermediateSchema` object identical to what the PDF and DOCX agents produce.
The existing EL-2.2, EL-2.3, unifier, and audit chain run on it normally.

**LangSmith as the primary AI observability layer** — OTel captures timing and
record counts; LangSmith captures what the AI actually did. For a migration
pipeline where every mapping decision is auditable, LangSmith is non-negotiable.
It gives you prompt-level visibility into every Strategy 4 call, every
compatibility check, every hierarchy classification, and every merge detection.
The `langsmith_run_id` stored per field mapping creates a direct link from any
human correction back to the exact LLM trace that produced the wrong result —
closing the feedback loop from production corrections into prompt improvements.
LangSmith datasets also power the regression test suite so any prompt change can
be validated against known-good migration runs before deployment.

---

## 13. How It Fits Into CLAUDE.md

Add to section 3 (repository structure):
```
svc-ai-schema-mapper/        ← NEW — Sprint 3
  port 8003
  LangGraph migration pipeline
  3-tier mapping + hierarchy detection + human review
  Outputs IntermediateSchema → svc-ingestion
```

Add to section 16 (services):
```
svc-ai-schema-mapper  | 8003 | new
```

Add migration 003 to section 21 Task 2.10:
```
003_add_migration_tables — migration_jobs, migration_field_mappings,
                            migration_hierarchy
```

The `❌ No LLM called per CSV/Excel row` rule in section 25 applies here too —
the LLM is called once per file (Strategy 4) and once for hierarchy
classification, never per row.

---

## 14. Changelog

### v1.1 — April 2026 (gap fixes)

5 gaps identified during requirements review, all resolved:

| # | Gap | Resolution | Sections affected |
|---|-----|-----------|-------------------|
| 1 | **Dataset description method** — LLM analysis of `df.head()` to generate per-column semantic descriptions was missing | Added `describe_dataset()` sub-step to Node 1. Column descriptions stored in `column_descriptions` state field and used by Strategy 4 and Tier 2 semantic mapper | §4 (MigrationState), §5 (Node 1), §11 (scaffold: `dataset_describer.py`) |
| 2 | **Multi-column merge mapping** — no support for combining multiple source columns into one target (e.g. `first_name + last_name → Name`) | Extended `FieldMapping` with `source_fields`, `merge_strategy` fields. Added `T2_multi_merge` tier. Merge applied at export time in `json_builder.py` | §4 (FieldMapping), §5 (Node 3 — new `detect_multi_column_merge()`), §10 (SQL schema), §11 (scaffold) |
| 3 | **1:N column compatibility checking** — explicit per-target-column check against all source columns was missing | Added `check_column_compatibility()` to Node 3 semantic mapper. Runs after embedding similarity for unmapped target fields | §5 (Node 3), §11 (scaffold: `column_compatibility.py`) |
| 4 | **PDF/Word mapping flow document** — full hierarchical processing and mapping flow document as a migration deliverable | Added `mapping_flow_doc.py` to export module. 6-section document covering source analysis, field mappings, merges, hierarchy, processing timeline, and validation. Generated in PDF + DOCX | §5 (Node 8), §7 (download endpoint), §10 (migration_jobs table), §11 (scaffold) |
| 5 | **Simple predefined rules format** — description-only rule format from requirements not accepted by the system | Added `adapt_simple_rules()` adapter in `mapping_doc_parser.py`. Auto-detects simple format and enriches via Haiku. Mixed formats supported | §8 (new sub-section), §11 (scaffold) |

**Additional state fields added:**
- `column_descriptions: dict[str, str]` — per-column LLM descriptions (Node 1 output)
- `dataset_summary: str` — prose summary of overall dataset (Node 1 output)
- `FieldMapping.source_fields: list[str] | None` — multi-column merge sources
- `FieldMapping.merge_strategy: str | None` — merge method (concat_space, coalesce, etc.)

**New files added to scaffold:**
- `matchers/dataset_describer.py` — `describe_dataset()` implementation
- `matchers/column_compatibility.py` — 1:N checker + multi-merge detector
- `export/mapping_flow_doc.py` — PDF/Word mapping flow document generator

**Database schema changes:**
- `migration_jobs` — added `mapping_flow_url TEXT`, `t2_multi_merge_count INT`
- `migration_field_mappings` — added `source_fields JSONB NULL`, `merge_strategy VARCHAR(50) NULL`
- `migration_field_mappings.tier` — added `T2_multi_merge` to allowed values

### v1.2 — April 2026 (LangSmith observability)

| # | Change | Sections affected |
|---|--------|-------------------|
| 1 | **LangSmith setup** — env vars, run naming convention, per-node tagging pattern added | §2a (new section), §11 (scaffold), §12 (design decisions) |
| 2 | **Per-node LangSmith annotations** — every node now has explicit LangSmith tracking notes covering which LLM calls are traced, what tags are applied, and what the trace is useful for debugging | §5 (all 9 nodes) |
| 3 | **LangSmith feedback loop** — human corrections in Node 4 write negative feedback to the originating LangSmith trace via `langsmith_run_id` | §2a, §5 (Node 4), §10 (DB schema) |
| 4 | **LangSmith datasets** — 5 evaluation datasets defined for regression testing each LLM-dependent step | §2a |
| 5 | **Graph wiring LangSmith note** — explains how the compiled graph trace appears in LangSmith as a tree | §6 |
| 6 | **New endpoint** `GET /api/migration/{id}/langsmith` — returns direct trace URL | §7 |
| 7 | **`langsmith_run_id` column** added to `migration_field_mappings` | §10 (DB schema) |
| 8 | **`langsmith` added to `pyproject.toml`** dependencies | §11 (scaffold) |

**Additional DB schema change:**
- `migration_field_mappings` — added `langsmith_run_id VARCHAR(100) NULL`
