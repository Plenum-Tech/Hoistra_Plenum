# svc-AI-Schema-Mapper — Architecture & Implementation (v2.0)

> **Universal AI-native CMMS data migration service**  
> Converts any CMMS export (CSV, Excel) into validated, hierarchically-resolved JSON that maps to the `plenum_cafm` target schema.  
> **Status:** PRODUCTION-READY (9-node LangGraph pipeline fully implemented with 3 HITL gates)  
> **Last updated:** April 2026 (v2.0 — Complete implementation documentation)

---

## What Changed from v1.2 → v2.0

### ✅ Fully Implemented (v1.2 → v2.0)
- All 9 nodes: ingest, deterministic_mapper, semantic_mapper, human_review, preprocess, hierarchy, verify_hierarchy, output_generator, write_to_platform
- PostgreSQL checkpointer for state persistence and resumability
- 4 database tables: migration_jobs, migration_field_mappings, migration_hierarchy, mapping_templates
- 3 HITL interrupt gates: Node 4 (mapping review), Node 7 (hierarchy verification), Node 9 (final confirmation)
- 18 testing endpoints for individual node execution
- Fiix platform connector integration (API + schema fetch)
- Multi-table support (per-table validation, metrics, cleaning)
- Dynamic schema introspection for default canonical fields
- Full WebSocket event streaming for real-time UI updates
- Complete validation gates (EL-M.1 through EL-M.9)

### ⚡ New in v2.0
- **SchemaIntrospectionService**: Dynamically introspect plenum_cafm schema at startup (replaces hardcoded canonical fields)
- **MappingService**: Reusable mapping templates per org/source/table
- **Multi-table Handling**: All nodes process tables independently with aggregated results
- **Fiix Platform Connector**: Direct API integration for schema extraction
- **Node 6 Enhancements**: Claude Sonnet-powered FK detection vs. simple heuristics
- **Node 8 Exports**: JSON (hierarchical) + CSV (flat) + SQL (ordered) + PDF report
- **ARQ Background Worker**: Async migration execution with job timeout handling
- **Mapping Flow Visualization**: Generate visual documentation of field mapping decisions

### 📝 Documentation Only (v1.2 → v2.0)
- LangSmith observability is fully available but documented as optional (env vars control it)
- Evaluation layers (EL-M.1 through EL-M.9) mapped to actual validation code in each node
- Detailed per-node state transitions documented

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                  svc-AI-Schema-Mapper (Port 8003)                │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  FastAPI Application with 28 Endpoints                   │   │
│  │  • 9 testing endpoints (Node 1-9 individual tests)       │   │
│  │  • 6 migration endpoints (start/status/approve/etc)      │   │
│  │  • 6 mapping template CRUD endpoints                     │   │
│  │  • 2 platform integration endpoints (Fiix)               │   │
│  │  • 1 WebSocket for real-time events                      │   │
│  │  • 1 LangSmith trace endpoint                            │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  LangGraph 9-Node State Machine (PostgreSQL Checkpointer) │   │
│  │  Node 1   → Parse file, detect format, generate descriptions │
│  │  Node 2   → 4-strategy deterministic field mapping          │
│  │  Node 3   → OpenAI embedding-based semantic matching        │
│  │  Node 4   → ⏸ GATE 1 HITL (flag + review)                  │
│  │  Node 5   → Data cleaning (dedup, nulls, type coercion)    │
│  │  Node 6   → Claude Sonnet FK + hierarchy detection         │
│  │  Node 7   → ⏸ GATE 2 HITL (hierarchy confirmation)         │
│  │  Node 8   → Generate JSON/CSV/SQL/PDF outputs              │
│  │  Node 9   → ⏸ GATE 3 HITL + svc-ingestion handoff          │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Supporting Services                                     │   │
│  │  • SchemaIntrospectionService (dynamic canonical fields) │   │
│  │  • MappingService (template CRUD + lookup)               │   │
│  │  • FiixConnector (Fiix API integration)                  │   │
│  │  • EmbeddingCache (pre-cached field embeddings)          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Data Layer (PostgreSQL plenum_cafm schema)             │   │
│  │  • migration_jobs (migration lifecycle)                 │   │
│  │  • migration_field_mappings (audit trail)               │   │
│  │  • migration_hierarchy (FK relationships)               │   │
│  │  • mapping_templates (reusable configs)                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                              │ IntermediateSchema
                              ▼
                    svc-ingestion (Port 8001)
```

---

## 2. Data Flow

### Happy Path (No HITL Required)

```
Customer Upload
     │
     ▼
  [Node 1] Parse file + generate descriptions
     │ ✅ EL-M.1: rows > 0, columns > 0
     ▼
  [Node 2] 4-strategy mapping
     │ ✅ EL-M.2: all confidences in 0–1, no duplicates
     ├─ Strategy 1: Exact match (0.99)
     ├─ Strategy 2: CMMS alias (0.95–0.98)
     ├─ Strategy 3: Regex pattern (0.90–0.94)
     └─ Strategy 4: Claude Haiku (0.85–0.92)
     │
     ├─ All fields resolved?
     │  └─ YES → skip Node 3, go to Node 5
     │
     ▼ NO
  [Node 3] Semantic embedding matching
     │ ✅ EL-M.3: embeddings computed
     │
     ├─ Any flags (0.65–0.84 confidence)?
     │  └─ YES → Must go to Node 4 (GATE 1)
     │
     ├─ Overall confidence < 0.80?
     │  └─ YES → Force Node 4 (EL-3.0)
     │
     ▼ NO flags & conf ≥ 0.80
  [Node 5] Data cleaning
     │ ✅ EL-M.5: dedup ratio ≥ 0.80
     ├─ Deduplication (drop exact duplicates)
     ├─ Null handling (coerce to defaults)
     ├─ Date normalization (ISO 8601)
     └─ FK column detection
     │
     ▼
  [Node 6] Hierarchy detection
     │ ✅ EL-M.6: no cycles in containment graph
     ├─ Claude Sonnet FK inference
     ├─ Implicit hierarchy detection (SAP-style codes)
     ├─ Cycle detection and resolution
     └─ Relationship classification
     │
     ├─ Any hierarchies to confirm?
     │  └─ NO → skip Node 7
     │
     ▼ YES
  [Node 7] Hierarchy verification (GATE 2)
     │ ⏸ HITL interrupt for confirmation
     │ Customer approves/corrects hierarchies
     │ ✅ EL-M.7: hierarchy confirmed
     │
     ▼
  [Node 8] Output generation
     │ ✅ EL-M.8: IntermediateSchema validated
     ├─ Nested JSON (FK-respecting)
     ├─ Flat CSV exports
     ├─ SQL INSERT statements (FK-ordered)
     ├─ PDF migration report
     └─ IntermediateSchema for svc-ingestion
     │
     ▼
  [Node 9] Final confirmation + handoff (GATE 3)
     │ ⏸ HITL interrupt for final approval
     │ POST IntermediateSchema to svc-ingestion
     │ ✅ EL-M.9: svc-ingestion acknowledged
     │
     ▼
  ✅ Migration Complete
     All data now in svc-ingestion pipeline
```

### With HITL Gates

```
[Node 4 GATE 1] ⏸ Mapping Review
  Entry: Node 3 has flagged items OR overall_confidence < 0.80
  Pause: Graph checkpoints to PostgreSQL
  Action: Customer reviews, approves, or overrides mappings
  Resume: Approved mappings committed to migration_field_mappings

[Node 7 GATE 2] ⏸ Hierarchy Verification
  Entry: Node 6 detected hierarchies
  Pause: Graph checkpoints to PostgreSQL
  Action: Customer reviews, approves, or corrects relationships
  Resume: Confirmed relationships committed to migration_hierarchy

[Node 9 GATE 3] ⏸ Final Confirmation
  Entry: Output generated, ready for handoff
  Pause: Graph checkpoints to PostgreSQL
  Action: Customer final sign-off before svc-ingestion write
  Resume: Handoff to svc-ingestion, status updated
```

---

## 3. 9-Node Pipeline (Detailed)

### Node 1: ingest_and_configure

**Purpose:** Parse CMMS file and generate semantic understanding

**Input:** 
- Customer-uploaded CSV/Excel file
- File bytes or Azure Blob URL
- CMMS name (Fiix, Maximo, SAP PM, Archibus, Custom)

**Execution:**
1. Detect encoding via `chardet` (CP1252, UTF-8, ISO-8859-1, etc.)
2. Detect CSV delimiter (comma, tab, semicolon)
3. Parse to Pandas DataFrames per sheet/table
4. Calculate table health: row counts, column counts, null percentages
5. **AI Enhancement:** Call Claude Haiku to generate semantic descriptions of columns:
   - Input: Column names, sample values
   - Output: Natural language descriptions for each column
6. Store both 5-row sample (`parsed_tables`) and full data (`full_tables`) in state

**State Output:**
```python
{
  "parsed_tables": {table: [5-row dict sample], ...},  # For quick preview
  "full_tables": {table: [all rows], ...},             # For downstream processing
  "row_count": 4891,
  "column_count": 47,
  "table_health": {
    "assets": {"row_count": 2000, "column_count": 15, "avg_null_percentage": 0.3},
    "work_orders": {"row_count": 2891, "column_count": 32, "avg_null_percentage": 1.2},
  },
  "column_descriptions": {
    "assets": {"ASSETNUM": "Unique asset identifier", "LOCATION": "Asset location"},
    ...
  },
  "dataset_summary": "Fiix CMMS export containing 2000 assets and 2891 work orders"
}
```

**Validation (EL-M.1):**
```python
if row_count == 0 or column_count == 0:
    raise ValidationError("File must have rows > 0 and columns > 0")
state["el_m1_passed"] = True
```

**Timing:** ~3–5 seconds (Claude call: ~1–2s)

---

### Node 2: deterministic_mapper

**Purpose:** Apply 4-strategy deterministic field matching

**Multi-table Support:** Process each table independently

**Per Field, 4 Strategies (In Order):**

1. **Strategy 1: Exact Match** (confidence: 0.99)
   - Column name matches canonical field exactly (case-insensitive)
   - E.g.: `ASSET_CODE` → `asset_code`

2. **Strategy 2: CMMS Alias Lookup** (confidence: 0.95–0.98)
   - Look up in `CMMS_ALIASES` dict (Maximo, Fiix, SAP, Archibus patterns)
   - E.g.: `WONUM` → `wo_code` (Maximo alias)
   - Also checks normalized form (underscores removed): `WO_NUMBER` → `wo_code`

3. **Strategy 3: Regex Pattern Matching** (confidence: 0.90–0.94)
   - Match against patterns like `^ASSET_.*`, `^PM_.*`, etc. in `PATTERNS` dict
   - E.g.: `ASSET_DESCRIPTION` matches `^ASSET_.*` → `asset_name` (with warning)

4. **Strategy 4: Claude Haiku Constrained** (confidence: 0.85–0.92)
   - If < 0.85 confidence from strategies 1–3, call Claude Haiku
   - Input: Column name, sample values, description from Node 1, top-5 closest canonical fields
   - Output: Best match + confidence score
   - Prompt enforces: "Return ONLY JSON: {canonical_field: str, confidence: float 0-1}"

**Canonical Fields (30+ predefined):**
```
Asset:     asset_code, asset_name, category, location_code, make, model,
           serial, criticality, install_date, status
Work Order: wo_code, wo_priority, wo_status, wo_type, maintenance_type,
           assigned_tech_id, cause_code, cost_parts_aed, cost_vendor_aed,
           fault_code, labor_minutes, resolution_code, sla_breached,
           sla_response_actual_mins, sla_response_target_mins, travel_minutes,
           vendor_id, responded_at, created_at, completed_at
PM/Schedule: sm_code, trigger_type, schedule_interval, sm_priority
Parts:      part_code, stock_on_hand, minimum_allowed_stock, supplier, bom_group_name
Users:      user_full_name, user_title, user_name, reports_to
Inspect:    inspector_name, inspection_date, inspection_location, finding_type, risk_level
Location:   site_id, site_name, site_type
```

**State Output:**
```python
{
  "tier1_mappings_by_table": {
    "assets": [
      FieldMapping(source="ASSETNUM", target="asset_code", confidence=0.99, tier="T1_exact"),
      FieldMapping(source="LOCATION", target="location_code", confidence=0.97, tier="T1_alias"),
    ],
    "work_orders": [...]
  },
  "unresolved_by_table": {
    "assets": ["CUSTOM_FIELD_1", "VENDOR_NOTES"],
    "work_orders": []
  },
  "t1_mapped_count": 43,
  "unresolved_count": 4
}
```

**Validation (EL-M.2):**
```python
# No duplicate target fields per table
for table, mappings in tier1_mappings_by_table.items():
    targets = [m.target for m in mappings]
    if len(targets) != len(set(targets)):
        raise ValidationError(f"Duplicate targets in {table}")

# All confidences in 0–1
for mapping in all_mappings:
    if not 0 <= mapping.confidence <= 1:
        raise ValidationError(f"Invalid confidence: {mapping.confidence}")

state["el_m2_passed"] = True
```

**Conditional Edge (to Node 3 vs. Node 5):**
```python
if unresolved_count > 0:
    # Has unresolved fields → go to Node 3 (semantic mapping)
    go_to_node = 3
else:
    # All resolved → skip semantic, go to Node 5 (preprocess)
    go_to_node = 5
```

**Timing:** ~1–2 seconds (mostly dict lookups, ~0.5s for Claude calls on unresolved fields)

---

### Node 3: semantic_mapper

**Purpose:** Embedding-based field matching for unresolved fields

**Entry:** If Node 2 has unresolved fields

**Execution:**

1. **For each unresolved field:**
   - Build text: `{field_name} | {description from Node 1} | {sample values}`
   - Example: `CUSTOM_VENDOR_CLASS | Custom equipment classification | ['A', 'B', 'Premium']`

2. **Embed text:**
   - Use OpenAI text-embedding-3-small (default, can use Voyage-3)
   - Cache at startup: Pre-compute embeddings for all 30+ canonical fields
   - Embed unresolved field text at runtime

3. **Cosine similarity matching:**
   - Find top-5 canonical fields by similarity
   - Confidence = similarity score (0–1)

4. **Classify by confidence:**
   - **≥ 0.85**: Auto-accept as T2_auto
   - **0.65–0.84**: Flag for human review as T2_flagged (suggest top-3)
   - **< 0.65**: Mark unmappable (preserve in raw_metadata, don't force mapping)

5. **Compute overall confidence:**
   - Weighted average: (T1_confidences + T2_auto_confidences) / total_fields
   - If < 0.80 → **Force Node 4 HITL** (EL-3.0 gate)

**State Output:**
```python
{
  "tier2_auto_by_table": {
    "assets": [
      FieldMapping(source="CUSTOM_VENDOR_CLASS", target="category", 
                   confidence=0.87, tier="T2_semantic", 
                   top_matches=[("category", 0.87), ("asset_type", 0.71)])
    ]
  },
  "tier2_flagged_by_table": {
    "assets": [
      FieldMapping(source="RESERVED_FOR", target=None, confidence=0.72, tier="T2_flagged",
                   top_matches=[("location_code", 0.72), ("site_id", 0.68), ("parent_asset", 0.65)])
    ]
  },
  "tier2_unmappable_by_table": {
    "assets": ["INTERNAL_NOTES"]  # < 0.65 confidence, will preserve in raw_metadata
  },
  "overall_confidence": 0.89,  # Weighted average
  "t2_auto_count": 1,
  "t2_flagged_count": 1,
  "t2_unmappable_count": 1
}
```

**Validation (EL-M.3):**
```python
# All confidence scores in 0–1
for tier, mappings_list in {
    "auto": tier2_auto_by_table,
    "flagged": tier2_flagged_by_table
}.items():
    for table, mappings in mappings_list.items():
        for m in mappings:
            if not 0 <= m.confidence <= 1:
                raise ValidationError(f"Invalid T2 confidence: {m.confidence}")

state["el_m3_passed"] = True

# EL-3.0: Force GATE 1 if overall < 0.80
if state["overall_confidence"] < 0.80:
    state["force_gate_1"] = True
```

**Conditional Edge (to Node 4 vs. Node 5):**
```python
if tier2_flagged_count > 0 or force_gate_1:
    # Has flagged items OR low overall confidence → go to Node 4 (GATE 1)
    go_to_node = 4
else:
    # All resolved with high confidence → skip GATE 1, go to Node 5
    go_to_node = 5
```

**Timing:** ~2–3 seconds (embeddings: ~1s, cosine similarity: <1s)

---

### Node 4: human_review_node (GATE 1) ⏸

**Purpose:** HITL interrupt for flagged/ambiguous mappings

**Entry Conditions:**
- `tier2_flagged_count > 0`, OR
- `overall_confidence < 0.80` (forced by EL-3.0), OR
- Manually requested by customer

**Execution:**

1. **Prepare review payload:**
   - Group flagged items by table
   - For each flagged field: source name, confidence, top-3 suggestions, rationale
   - If forced: add alert explaining low overall confidence

2. **Call `interrupt()`:**
   - Graph pauses, state checkpointed to PostgreSQL
   - Returns to API caller with review data
   - Customer can close browser and resume later (state persisted)

3. **Customer decides (via UI form):**
   - For each flagged field: Accept / Reject / Override with custom field
   - Provide optional notes explaining decision

4. **Resume with approvals:**
   - API receives decisions via `/api/migration/{migration_id}/approve`
   - Decisions validated (action + source_field + target_field required)
   - Approved mappings committed to `migration_field_mappings` table with `reviewer_id`

**State Output:**
```python
{
  "tier2_human_decisions_by_table": {
    "assets": [
      FieldMapping(source="RESERVED_FOR", target="location_code", 
                   confidence=0.72, tier="T2_human", reviewed_by="user_uuid",
                   reviewed_at="2026-04-10T10:30:00Z", notes="Matches location usage pattern")
    ]
  },
  "tier2_human_count": 1,
  "gate_1_completed": True
}
```

**Validation (EL-M.4):**
```python
# Each approval has required fields
for table, approvals in tier2_human_decisions_by_table.items():
    for approval in approvals:
        assert approval.source_field is not None
        assert approval.target_field is not None
        assert approval.reviewer_id is not None

state["el_m4_passed"] = True
```

**Conditional Edge (to Node 5):**
```python
# After resume with approvals, always proceed to Node 5 (preprocess)
go_to_node = 5
```

**Timing:** Depends on customer response time (seconds to hours)

---

### Node 5: preprocess_and_validate

**Purpose:** Data cleaning before hierarchy detection

**Multi-table Support:** Each table processed independently

**Execution:**

1. **Deduplication:**
   - Drop exact-duplicate rows (all columns match)
   - Track dropped row count per table

2. **Null Handling:**
   - Numeric fields: replace `NULL` → `0`
   - Text fields: replace `NULL` → `""` (empty string)
   - Date fields: leave as `NULL` (no valid default)

3. **Date Normalization:**
   - Try 5 common formats: ISO 8601, MM/DD/YYYY, DD/MM/YYYY, YYYY-MM-DD, custom
   - Normalize all to ISO 8601 (`YYYY-MM-DD`)
   - If ambiguous: leave as-is, log warning

4. **Data Type Coercion:**
   - String numerics: `"123"` → `123`
   - Numeric strings: `123` → `"123"` (if target is text)
   - Boolean: `"Yes"/"No"` → `true`/`false`

5. **JSON Schema Validation (warnings only):**
   - Validate each row against plenum_cafm table schema
   - Log warnings for type mismatches, no blocking

6. **FK Pre-check:**
   - Scan for columns matching FK patterns: `_id`, `_num`, `_ref`, `_code`, `_key`
   - Store as `detected_fk_columns` for Node 6

**State Output:**
```python
{
  "cleaned_tables": {
    "assets": [
      {"asset_code": "MOB-001", "asset_name": "Chiller", "location_code": "SITE-A", ...},
      ...  # 1,999 rows after dedup (1 duplicate removed)
    ],
    "work_orders": [...]  # 2,891 rows, no duplicates
  },
  "row_count_post_dedup_by_table": {"assets": 1999, "work_orders": 2891},
  "dedup_drop_count_by_table": {"assets": 1, "work_orders": 0},
  "detected_fk_columns": {
    "work_orders": ["asset_code", "assigned_tech_id"],
    "assets": ["parent_asset_code"]
  },
  "data_quality_warnings": [
    "assets: 5 rows have null 'install_date' (expected for new assets)",
    "work_orders: 12 rows have non-standard cost formats (converted to 0)"
  ]
}
```

**Validation (EL-M.5):**
```python
# Deduplication ratio must be ≥ 0.80 (max 20% data loss)
for table, dropped in dedup_drop_count_by_table.items():
    original = row_count[table]
    post_dedup = row_count_post_dedup_by_table[table]
    dedup_ratio = post_dedup / original
    if dedup_ratio < 0.80:
        raise ValidationError(f"{table}: {dropped}/{original} duplicates removed (ratio {dedup_ratio:.2%})")

state["el_m5_passed"] = True
```

**Conditional Edge (to Node 6):**
```python
# Always proceed to Node 6 (hierarchy detection)
go_to_node = 6
```

**Timing:** ~1–2 seconds (dependent on row count; 5K rows typically <1s)

---

### Node 6: resolve_hierarchy

**Purpose:** LLM-powered FK and containment relationship detection

**Execution:**

1. **Build schema context:**
   - All table names, column names, data types
   - Sample row data (first 10 rows per table)
   - Detected FK columns from Node 5

2. **Claude Sonnet semantic inference:**
   - Prompt: "Analyze this CMMS schema. What are the FK relationships and containment hierarchies?"
   - Sonnet infers relationships based on:
     - Column names (`asset_id`, `site_code`, etc.)
     - Sample data (matching patterns, parent/child values)
     - Domain knowledge of CMMS structures
   - Output: JSON list of inferred relationships with types and confidence

3. **Validate against actual data:**
   - For each FK: sample 100 values from child table
   - Check if 80%+ match values in parent table
   - Update confidence based on match rate

4. **Detect cycles:**
   - Build graph of FK relationships
   - Run cycle detection algorithm
   - Report any cycles (self-references, circular chains)

5. **Resolve self-referencing trees:**
   - E.g., `assets.parent_asset_code` → `assets.asset_code` (recursive)
   - Special handling: don't try to normalize tree

6. **Classify relationships:**
   - **CONTAINMENT**: Parent fully contains child (e.g., site → location → asset)
   - **REFERENCE**: Parent referenced but not containing child
   - **OWNERSHIP**: Ownership relationship (e.g., equipment owned by vendor)
   - **PART_OF**: Child is part of parent (e.g., task part of WO)
   - **SELF_REF**: Self-referencing tree (e.g., parent asset)

7. **Detect implicit hierarchies:**
   - SAP-style codes: `PLANT-LINE-UNIT` breakdown
   - Numeric hierarchies: `1.1.1` → `1.1` → `1`
   - Prefix patterns: `SITE_A.LOC_B.ASSET_C`

**State Output:**
```python
{
  "fk_candidates": [
    {
      "source_table": "work_orders",
      "source_column": "asset_code",
      "target_table": "assets",
      "target_column": "asset_code",
      "relationship_type": "REFERENCE",
      "confidence": 0.95,
      "reasoning": "100% of sampled WO asset_codes exist in assets table"
    },
    ...
  ],
  "confirmed_hierarchies": [
    {
      "relationship": "site_id → location_code → asset_code",
      "type": "CONTAINMENT",
      "confidence": 0.92,
      "confirmed": False  # Awaiting customer confirmation (Node 7)
    }
  ],
  "hierarchy_cycles": [
    ["asset_code", "parent_asset_code"]  # Self-reference, OK but noted
  ],
  "implicit_hierarchies": [],
  "containment_hierarchy_by_table": {  # Will be finalized in Node 7
    "sites": {"depth": 0},
    "locations": {"depth": 1, "parent": "sites"},
    "assets": {"depth": 2, "parent": "locations"},
    "work_orders": {"depth": 2, "parent": "assets"}
  }
}
```

**Validation (EL-M.6):**
```python
# No unexpected cycles in containment graph (self-refs OK)
for cycle in hierarchy_cycles:
    if len(cycle) > 1:  # Multi-node cycle (not self-ref)
        raise ValidationError(f"Unexpected cycle: {' → '.join(cycle)}")

state["el_m6_passed"] = True
```

**Conditional Edge (to Node 7 vs. Node 8):**
```python
if confirmed_hierarchies:
    # Has hierarchies to confirm → go to Node 7 (GATE 2)
    go_to_node = 7
else:
    # No hierarchies → skip Node 7, go to Node 8 (output generation)
    go_to_node = 8
```

**Timing:** ~2–4 seconds (Claude call: ~2–3s, validation: <1s)

---

### Node 7: verify_hierarchy (GATE 2) ⏸

**Purpose:** HITL interrupt for hierarchy confirmation

**Entry Conditions:**
- `confirmed_hierarchies` has entries from Node 6

**Execution:**

1. **Prepare hierarchy summary:**
   - Visual tree: site → location → asset → work_order
   - Statistics: counts per level, orphan counts, cycle notes
   - Confidence scores for each relationship

2. **Call `interrupt()`:**
   - Graph pauses, state checkpointed to PostgreSQL
   - Returns to API caller with hierarchy visualization
   - Customer can close browser and resume later

3. **Customer decides:**
   - Approve hierarchies as-is
   - Correct parent/child relationships
   - Mark relationships as invalid if needed
   - Resolve any cycles

4. **Resume with confirmations:**
   - API receives confirmations via `/api/migration/{migration_id}/approve`
   - Approved hierarchies committed to `migration_hierarchy` table

**State Output:**
```python
{
  "hierarchies_approved": 5,
  "cycles_resolved": 0,
  "confirmed_hierarchies": [  # Updated with customer decisions
    {
      "source_table": "locations",
      "source_column": "site_id",
      "target_table": "sites",
      "target_column": "site_id",
      "relationship_type": "CONTAINMENT",
      "confidence": 0.92,
      "customer_confirmed": True,
      "confirmed_at": "2026-04-10T11:00:00Z"
    },
    ...
  ],
  "containment_hierarchy": {
    "sites": {
      "children": {
        "locations": {
          "children": {
            "assets": {
              "children": {
                "work_orders": {"children": {}}
              }
            }
          }
        }
      }
    }
  },
  "gate_2_completed": True
}
```

**Validation (EL-M.7):**
```python
# All hierarchies confirmed or explicitly rejected
for hierarchy in confirmed_hierarchies:
    assert hierarchy.customer_confirmed in [True, False]

state["el_m7_passed"] = True
```

**Conditional Edge (to Node 8):**
```python
# Always proceed to Node 8 (output generation)
go_to_node = 8
```

**Timing:** Depends on customer response time (seconds to minutes)

---

### Node 8: generate_output

**Purpose:** Generate all output formats and build IntermediateSchema

**Execution:**

1. **Nested JSON Export:**
   - Respect FK relationships and containment hierarchy
   - Output: `{sites: [{site_id, name, locations: [{location_id, ...}, ...]}]}`
   - Full hierarchy preserved

2. **Flat CSV Exports:**
   - One CSV per table
   - Columns in order: FK columns first, then all mapped fields
   - Unmappable fields preserved in `_raw_metadata` JSON column

3. **SQL INSERT Statements:**
   - Generate in FK-dependency order (parents before children)
   - Handle FKs correctly (insert sites before locations, etc.)
   - Output as single SQL script

4. **PDF Migration Report:**
   - Title: "{CMMS_NAME} → plenum_cafm Migration Report"
   - Sections:
     - Run metadata (date, file, organization)
     - Mapping summary (T1/T2 counts, confidence histogram)
     - Unmapped fields (with notes on why)
     - Hierarchy diagram (ASCII tree or simple layout)
     - Data quality: dedup stats, null handling, FK validation results
     - Audit trail: every field mapping with confidence + reviewer + notes

5. **Mapping Flow Visualization:**
   - Optional: Generate SVG/image showing field mapping flow
   - Shows: Input columns → Tier 1 mapping → Tier 2 matching → Final target fields

6. **IntermediateSchema (For svc-ingestion handoff):**
   - Pydantic model matching `svc-ingestion/src/shared/intermediate_schema.py`
   - Fields:
     - `ingestion_id` (UUID)
     - `source_type` ("csv" | "excel")
     - `source_filename`, `source_blob_url`
     - `entities` (dict with nested tables/rows)
     - `confidence` (overall score, per-field scores)
     - `audit` (token counts, cost, processing time)

7. **Upload to Azure Blob Storage:**
   - Create signed URLs (1-week expiry)
   - Return URLs in state

**State Output:**
```python
{
  "output_json_url": "https://plenumstorage.blob.core.windows.net/...?sv=...",
  "output_csv_url": "https://plenumstorage.blob.core.windows.net/...?sv=...",
  "output_sql_url": "https://plenumstorage.blob.core.windows.net/...?sv=...",
  "migration_report_url": "https://plenumstorage.blob.core.windows.net/...?sv=...",
  "mapping_flow_url": None,  # Optional, only if generated
  "intermediate_schema": {
    "ingestion_id": "uuid",
    "source_type": "excel",
    "source_filename": "fiix_export_2026-04-10.xlsx",
    "entities": {
      "assets": [
        {"asset_code": "MOB-001", "asset_name": "Chiller", ...},
        ...
      ],
      "work_orders": [...]
    },
    "confidence": {"overall": 0.89, "per_field": {...}},
    "audit": {"tokens_in": 4521, "tokens_out": 892, "cost_usd": 0.021}
  }
}
```

**Validation (EL-M.8):**
```python
# IntermediateSchema must pass Pydantic validation
from svc_ingestion.shared.intermediate_schema import IntermediateSchema

try:
    schema = IntermediateSchema(**intermediate_schema)
except ValidationError as e:
    raise ValidationError(f"IntermediateSchema invalid: {e}")

state["el_m8_passed"] = True
```

**Conditional Edge (to Node 9):**
```python
# Always proceed to Node 9 (final handoff)
go_to_node = 9
```

**Timing:** ~3–5 seconds (mostly file generation and upload)

---

### Node 9: write_to_platform (GATE 3) ⏸

**Purpose:** Final confirmation and handoff to svc-ingestion

**Entry Conditions:**
- IntermediateSchema built and validated (EL-M.8 passed)

**Execution:**

1. **Prepare final summary:**
   - Migration statistics (tables, rows, mappings)
   - Confidence scores
   - Links to generated outputs
   - Alert if any warnings present

2. **Call `interrupt()`:**
   - Graph pauses, state checkpointed to PostgreSQL
   - Returns to API caller with final summary
   - Customer can review before final commit

3. **Customer gives final approval:**
   - API receives confirmation via `/api/migration/{migration_id}/approve`
   - Confirmation includes: `action: "write"` + customer sign-off

4. **Handoff to svc-ingestion:**
   - POST IntermediateSchema to `svc-ingestion:8001/ingest`
   - Include migration metadata (migration_id, organization_id, source_system)
   - Wait for acknowledgment (timeout: 30s)

5. **Update migration_jobs:**
   - Set `status = "complete"`
   - Set `completed_at = now()`
   - Store svc-ingestion response

**State Output:**
```python
{
  "handoff_status": "acknowledged",  # pending | sent | acknowledged | failed
  "svc_ingestion_response": {
    "status": "accepted",
    "ingestion_id": "uuid",
    "message": "IntermediateSchema accepted, pipeline started"
  },
  "gate_3_completed": True
}
```

**Validation (EL-M.9):**
```python
# svc-ingestion must acknowledge
if handoff_status != "acknowledged":
    raise ValidationError(f"svc-ingestion handoff failed: {handoff_status}")

state["el_m9_passed"] = True
```

**Conditional Edge (Exit):**
```python
# Migration complete
return {"status": "complete", "message": "Handed off to svc-ingestion"}
```

**Timing:** ~1–2 seconds (HTTP call: ~1s)

---

## 4. 28 API Endpoints

### Main Migration Endpoints (6)

| Endpoint | Method | Purpose | Status |
|----------|--------|---------|--------|
| `/api/migration/start` | POST | Start new migration job | ✅ Implemented |
| `/api/migration/{migration_id}/status` | GET | Get current status + statistics | ✅ Implemented |
| `/api/migration/{migration_id}/approve` | POST | Submit HITL approvals (all 3 gates) | ✅ Implemented |
| `/api/migration/{migration_id}/audit` | GET | Get complete audit trail | ✅ Implemented |
| `/api/migration/{migration_id}/download/{format}` | GET | Download output (json/csv/sql/pdf) | ✅ Implemented |
| `/api/migration/list` | GET | List migrations by org/status | ✅ Implemented |
| `/api/migration/{migration_id}/cancel` | DELETE | Cancel running migration | ✅ Implemented |
| `/api/migration/{migration_id}/langsmith` | GET | Get LangSmith trace URL | ✅ Implemented |

### Testing Endpoints (9 — One per Node)

| Endpoint | Method | Purpose | Input | Status |
|----------|--------|---------|-------|--------|
| `/api/testing/upload` | POST | Node 1 only: Parse + describe | File, CMMS name | ✅ |
| `/api/testing/ingest-with-mapper` | POST | Nodes 1–2: With custom JSON mapper | File, JSON mapper | ✅ |
| `/api/testing/ingest-with-semantic` | POST | Nodes 1–3: Full semantic mapping | File ± mapper | ✅ |
| `/api/testing/human-review` | POST | Node 4 test: HITL approvals | Flagged items + decisions | ✅ |
| `/api/testing/preprocess` | POST | Node 5 test: Data cleaning | Tables + mappings | ✅ |
| `/api/testing/resolve-hierarchy` | POST | Node 6 test: FK detection | Tables + mappings | ✅ |
| `/api/testing/verify-hierarchy` | POST | Node 7 test: Hierarchy confirmation | Hierarchies + cycles | ✅ |
| `/api/testing/generate-output` | POST | Node 8 test: Output generation | All mappings + tables | ✅ |
| `/api/testing/write-output` | POST | Node 9 test: Final handoff | IntermediateSchema | ✅ |

### Mapping Template CRUD (6)

| Endpoint | Method | Purpose | Status |
|----------|--------|---------|--------|
| `/api/mappings` | POST | Create new mapping template | ✅ |
| `/api/mappings` | GET | List templates for organization | ✅ |
| `/api/mappings/{mapping_id}` | GET | Retrieve specific template | ✅ |
| `/api/mappings/lookup/{source_system}/{table_name}` | GET | Auto-lookup active mapping | ✅ |
| `/api/mappings/{mapping_id}` | PUT | Update template | ✅ |
| `/api/mappings/{mapping_id}` | DELETE | Soft delete template | ✅ |

### Platform Integration (2 — Fiix)

| Endpoint | Method | Purpose | Status |
|----------|--------|---------|--------|
| `/api/platforms/fiix/test-connection` | GET | Test Fiix API connectivity | ✅ |
| `/api/platforms/fiix/fetch-schema` | GET | Fetch Fiix platform schema | ✅ |

### Utilities (4)

| Endpoint | Method | Purpose | Status |
|----------|--------|---------|--------|
| `GET /health` | GET | Health check | ✅ |
| `GET /metrics` | GET | Prometheus metrics | ✅ |
| `GET /api/debug/fiix-config` | GET | Show Fiix config (DEBUG) | ✅ |
| `WebSocket /ws/migration/{migration_id}` | WS | Real-time event streaming | ✅ |

---

## 5. Database Schema (4 Tables)

All in `plenum_cafm` schema, managed by Alembic migration `003_add_migration_tables.py`.

### migration_jobs

Primary table tracking migration lifecycle.

```sql
CREATE TABLE migration_jobs (
  id UUID PRIMARY KEY,
  organization_id UUID NOT NULL,
  cmms_name VARCHAR(50),
  source_filename VARCHAR(255),
  source_blob_url VARCHAR(512),
  status VARCHAR(30),  -- running|awaiting_review|complete|failed|cancelled
  current_step INT,    -- 1–9 (which node)
  progress_pct FLOAT,
  
  -- Mapping statistics
  t1_mapped_count INT,
  t2_auto_count INT,
  t2_human_count INT,
  t2_multi_merge_count INT,
  unmapped_count INT,
  total_fields INT,
  total_records INT,
  
  -- Hierarchy statistics
  orphan_count INT,
  cycle_count INT,
  
  -- Output URLs
  output_json_url VARCHAR(512),
  output_csv_url VARCHAR(512),
  output_sql_url VARCHAR(512),
  migration_report_url VARCHAR(512),
  mapping_flow_url VARCHAR(512),
  
  -- Error tracking
  error_message TEXT,
  error_node INT,
  error_timestamp TIMESTAMPTZ,
  
  -- Lifecycle
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  
  -- Audit
  uploaded_by UUID,
  created_at TIMESTAMPTZ DEFAULT now(),
  
  FOREIGN KEY (organization_id) REFERENCES organizations(id),
  INDEX (organization_id, status),
  INDEX (status),
  INDEX (created_at)
);
```

### migration_field_mappings

Immutable audit trail of every field mapping decision.

```sql
CREATE TABLE migration_field_mappings (
  id UUID PRIMARY KEY,
  migration_id UUID NOT NULL,
  
  -- Source field
  source_field VARCHAR(255),
  source_fields JSONB,        -- for multi-column merges
  merge_strategy VARCHAR(50), -- concat_space|concat_comma|coalesce|concat_dash
  
  -- Target field
  target_field VARCHAR(255),
  confidence FLOAT,
  
  -- Decision tracking
  tier VARCHAR(50),  -- T1_exact|T1_alias|T1_regex|T1_llm|T2_semantic|T2_human|T2_multi_merge|unmapped
  rationale TEXT,
  sample_values JSONB,
  transformation VARCHAR(100),
  
  -- Human review
  reviewer_id UUID,
  reviewed_at TIMESTAMPTZ,
  
  -- LangSmith tracing
  langsmith_run_id VARCHAR(100),
  
  -- Lifecycle
  decided_at TIMESTAMPTZ DEFAULT now(),
  
  FOREIGN KEY (migration_id) REFERENCES migration_jobs(id) ON DELETE CASCADE,
  INDEX (migration_id, target_field),
  INDEX (migration_id, tier)
);
```

### migration_hierarchy

FK & containment relationships detected.

```sql
CREATE TABLE migration_hierarchy (
  id UUID PRIMARY KEY,
  migration_id UUID NOT NULL,
  
  -- Relationship definition
  source_table VARCHAR(100),
  source_column VARCHAR(100),
  target_table VARCHAR(100),
  target_column VARCHAR(100),
  
  -- Classification
  relationship_type VARCHAR(50),  -- CONTAINMENT|REFERENCE|OWNERSHIP|PART_OF|SELF_REF
  direction VARCHAR(100),
  confidence FLOAT,
  data_match_rate FLOAT,
  reasoning TEXT,
  
  -- Customer confirmation
  customer_confirmed BOOLEAN DEFAULT false,
  confirmed_at TIMESTAMPTZ,
  
  -- Lifecycle
  detected_at TIMESTAMPTZ DEFAULT now(),
  
  FOREIGN KEY (migration_id) REFERENCES migration_jobs(id) ON DELETE CASCADE,
  INDEX (migration_id, relationship_type),
  INDEX (migration_id, customer_confirmed)
);
```

### mapping_templates

Reusable CMMS mappings per org/source/table.

```sql
CREATE TABLE mapping_templates (
  id UUID PRIMARY KEY,
  organization_id UUID NOT NULL,
  source_system VARCHAR(100),  -- fiix|maximo|sap_pm|archibus|custom
  table_name VARCHAR(100),
  version INT DEFAULT 1,
  name VARCHAR(255),
  
  -- Configuration
  config_json JSONB,           -- Full JsonMapperConfig
  is_active BOOLEAN DEFAULT true,
  
  -- Audit
  created_by UUID,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ,
  
  FOREIGN KEY (organization_id) REFERENCES organizations(id),
  UNIQUE (organization_id, source_system, table_name, is_active),
  INDEX (organization_id, source_system, is_active)
);
```

---

## 6. Key Services

### SchemaIntrospectionService

**File:** `src/services/schema_introspection.py`

Dynamically introspect plenum_cafm schema to auto-generate canonical fields mapping (replaces hardcoded lists).

**Usage (at app startup):**
```python
service = SchemaIntrospectionService(db_url)
mapper_config = await service.build_default_mapper_config()
# Returns: {"version": "1.0", "source_system": "plenum_cafm", 
#           "canonical_fields": {...}, "vendor_aliases": {...}}
```

**Methods:**
- `build_default_mapper_config()` → Build JsonMapperConfig from live DB schema
- `_get_canonical_fields()` → Extract all columns from plenum_cafm tables with descriptions
- `_build_vendor_aliases()` → Reverse CMMS_ALIASES to {canonical: [aliases]}
- `_generate_description()` → AI-generated column descriptions

**Used by:** App startup (line 160–170 in app.py)

---

### MappingService

**File:** `src/services/mapping_service.py`

CRUD operations and lookup for reusable mapping templates.

**Methods:**
- `create_mapping()` → Store new template in DB
- `get_mapping()` → Retrieve by ID
- `list_mappings()` → List for org (paginated)
- `lookup_mapping(org_id, source_system, table_name)` → Get active mapping
- `update_mapping()` → Update template config
- `delete_mapping()` → Soft delete (is_active = false)

**Used by:** 
- `/api/mappings/*` endpoints
- Node 2 (auto-lookup for customer org/source)

---

### FiixConnector

**File:** `src/connectors/fiix_connector.py`

Direct Fiix CMMS API integration.

**Classes:**
- `FiixAPI` — API client for Fiix
- `FiixSchemaConnector` — Schema extraction

**Methods:**
- `test_connection()` → Verify API credentials work
- `fetch_schema()` → Get platform schema as CSV bytes

**Used by:**
- `/api/platforms/fiix/*` endpoints
- Platform setup workflows (not yet implemented as separate process)

---

### EmbeddingCache

**File:** `src/embeddings.py`

Pre-compute and cache OpenAI embeddings for canonical fields.

**Initialization (at app startup):**
```python
await initialize_canonical_embeddings(openai_client, canonical_fields)
# Pre-caches all 30+ canonical field embeddings to memory
```

**Methods:**
- `embed_text()` → Embed single text string
- `embed_texts_batch()` → Batch embed multiple strings
- `find_top_matches()` → Cosine similarity matching (returns top-5)

**Used by:** Node 3 (semantic mapper)

---

## 7. Directory & File Structure (Complete)

```
src/
├── app.py                      # 2,549 lines — FastAPI app + 28 endpoints
├── config.py                   # Settings configuration
├── db.py                       # Async SQLAlchemy session factory
├── embeddings.py               # OpenAI embedding utilities
├── schemas.py                  # 50+ Pydantic models for requests/responses
├── worker.py                   # ARQ background job worker
│
├── connectors/
│   ├── fiix_connector.py       # Fiix CMMS API integration
│   └── __init__.py
│
├── models/
│   ├── migration.py            # 4 SQLAlchemy ORM models
│   └── __init__.py
│
├── services/
│   ├── mapping_service.py      # Mapping template CRUD
│   ├── schema_introspection.py # Dynamic schema introspection
│   └── __init__.py
│
├── graph/
│   ├── migration_graph.py      # LangGraph 9-node compilation
│   ├── state.py                # MigrationState TypedDict (141 fields)
│   └── nodes/
│       ├── ingest_node.py      # Node 1: Parse + describe
│       ├── deterministic_mapper.py  # Node 2: 4-strategy mapping
│       ├── semantic_mapper.py   # Node 3: Embedding matching
│       ├── human_review_node.py # Node 4: GATE 1 HITL
│       ├── preprocess_node.py   # Node 5: Data cleaning
│       ├── hierarchy_node.py    # Node 6: FK detection
│       ├── verify_hierarchy_node.py # Node 7: GATE 2 HITL
│       ├── output_generator_node.py # Node 8: Output generation
│       ├── write_node.py        # Node 9: GATE 3 + handoff
│       └── __init__.py
│
├── hierarchy/
│   ├── fk_scanner.py           # FK pattern scanning
│   ├── fk_validator.py         # FK validation vs. data
│   ├── implicit_hierarchy.py   # SAP-style code hierarchies
│   ├── cycle_detector.py       # Cycle detection
│   ├── tree_resolver.py        # Self-referencing tree handling
│   └── __init__.py
│
├── matchers/
│   ├── cmms_aliases.py         # CMMS field aliases (T1 strat 2)
│   ├── regex_patterns.py       # Regex patterns (T1 strat 3)
│   ├── dataset_describer.py    # Claude descriptions
│   ├── mapping_doc_parser.py   # Parse customer mapping docs
│   └── __init__.py
│
├── export/
│   ├── json_builder.py         # Nested JSON (FK-respecting)
│   ├── csv_exporter.py         # Flat CSV exports
│   ├── sql_exporter.py         # SQL INSERT statements
│   ├── report_generator.py     # PDF migration report
│   ├── intermediate_schema_builder.py # IntermediateSchema construction
│   └── __init__.py
│
└── api/
    ├── mappings.py             # Mapping template CRUD routes
    └── __init__.py

alembic/
├── alembic.ini
└── versions/
    └── 003_add_migration_tables.py  # Create 4 tables

tests/
├── test_api_endpoints.py
├── test_e2e_pipeline.py
├── test_websocket.py
└── conftest.py

Dockerfile                   # Python 3.12, uvicorn
docker-compose.yml          # Service definition
requirements.txt            # Dependencies
pyproject.toml              # Project config
```

---

## 8. Validation Gates (EL-M.1 through EL-M.9)

| Gate | Node | Check | Blocks | Fix |
|------|------|-------|--------|-----|
| **EL-M.1** | Node 1 | rows > 0 AND columns > 0 | ✅ Node 1 | Re-upload valid file |
| **EL-M.2** | Node 2 | No duplicate targets, conf ∈ [0,1] | ✅ Node 2 | Manual mapping review |
| **EL-M.3** | Node 3 | Embeddings computed, conf ∈ [0,1] | ✅ Node 3 | Embedding API issue |
| **EL-3.0** | Node 3→4 | overall_confidence ≥ 0.80 | ⏸ Node 4 | Customer approves flagged items |
| **EL-M.4** | Node 4 | Each approval: source + target + action | ✅ Node 4 | Resubmit approvals |
| **EL-M.5** | Node 5 | dedup_ratio ≥ 0.80 (≤20% loss) | ✅ Node 5 | Data quality issue |
| **EL-M.6** | Node 6 | No cycles (self-refs OK) | ✅ Node 6 | Resolve cycle manually |
| **EL-M.7** | Node 7 | Hierarchy customer_confirmed | ⏸ Node 7 | Customer approves hierarchies |
| **EL-M.8** | Node 8 | IntermediateSchema Pydantic valid | ✅ Node 8 | Schema mismatch (dev issue) |
| **EL-M.9** | Node 9 | svc-ingestion API acknowledged | ⏸ Node 9 | Customer final approval |

---

## 9. Configuration (src/config.py)

**Database:**
- `DB_URL` (PostgreSQL + asyncpg)
- `DB_POOL_SIZE` (default: 20)
- `DB_MAX_OVERFLOW` (default: 10)

**API:**
- `HOST` (default: 0.0.0.0)
- `PORT` (default: 8003)
- `CORS_ORIGINS` (default: ["*"])

**Claude AI:**
- `ANTHROPIC_API_KEY` (required)
- `CLAUDE_DEFAULT_MODEL` (default: claude-haiku-4-5)

**OpenAI (embeddings):**
- `OPENAI_API_KEY` (required)
- `EMBEDDING_PROVIDER` (openai | voyage, default: openai)
- `EMBEDDING_MODEL` (default: text-embedding-3-small)

**LangSmith (optional tracing):**
- `LANGSMITH_API_KEY`
- `LANGSMITH_PROJECT` (default: cafm-ai-schema-mapper)
- `LANGSMITH_ENDPOINT` (default: https://api.smith.langchain.com)
- `LANGSMITH_TRACING` (default: false)

**Azure Blob:**
- `AZURE_STORAGE_CONNECTION_STRING` (required)
- `AZURE_BLOB_CONTAINER_NAME` (default: plenum-agentic-ai-attachments)

**svc-ingestion Handoff:**
- `SVC_INGESTION_URL` (default: http://svc-ingestion:8001)

**Fiix Platform (optional):**
- `FIIX_ENABLED` (default: false)
- `FIIX_SUBDOMAIN` (e.g., demo.onfiix.com)
- `FIIX_APP_KEY`, `FIIX_ACCESS_KEY`, `FIIX_SECRET_KEY`
- `FIIX_TIMEOUT` (default: 10s)

**Limits:**
- `MAX_FILE_SIZE_MB` (default: 500)
- `MAX_ROWS_PER_TABLE` (default: 5000000)
- `MAX_UNRESOLVED_FIELDS_BEFORE_ERROR` (default: 20)

---

## 10. Key Implementation Details

### Tier 1 Mapping (4 Strategies)

**Strategy 1: Exact Match**
```python
# Case-insensitive direct match
if column_name.lower() == canonical_field.lower():
    return FieldMapping(confidence=0.99, tier="T1_exact")
```

**Strategy 2: CMMS Alias**
```python
# Look up in CMMS_ALIASES dict (Maximo, Fiix, SAP, Archibus)
from matchers.cmms_aliases import CMMS_ALIASES
if column_name.lower() in CMMS_ALIASES:
    target = CMMS_ALIASES[column_name.lower()]
    return FieldMapping(target=target, confidence=0.97, tier="T1_alias")
```

**Strategy 3: Regex Pattern**
```python
# Match against PATTERNS dict
from matchers.regex_patterns import PATTERNS
for pattern, target in PATTERNS.items():
    if re.match(pattern, column_name):
        return FieldMapping(target=target, confidence=0.92, tier="T1_regex")
```

**Strategy 4: Claude Haiku**
```python
# Constrained LLM call
response = await claude.messages.create(
    model="claude-haiku-4-5",
    max_tokens=100,
    system="Return ONLY valid JSON: {canonical_field: str, confidence: float}",
    messages=[{
        "role": "user",
        "content": f"Map {column_name} (samples: {samples}) to canonical field"
    }]
)
# Parse JSON, validate confidence
```

### Tier 2 Mapping (Embeddings)

```python
# For each unresolved field
unresolved_text = f"{field_name} | {description} | {sample_values}"

# Embed via OpenAI
embedding = await openai_client.embeddings.create(
    model="text-embedding-3-small",
    input=unresolved_text
)

# Cosine similarity vs. pre-cached canonical embeddings
similarities = cosine_similarity([embedding], cached_embeddings)[0]
top_5 = sorted(zip(canonical_fields, similarities), key=lambda x: x[1], reverse=True)[:5]

# Classify
if top_5[0][1] >= 0.85:
    tier = "T2_semantic"  # Auto-accept
elif top_5[0][1] >= 0.65:
    tier = "T2_flagged"   # Flag for review
else:
    tier = "unmappable"   # Can't map
```

### Multi-table Handling

All nodes 2–7 process tables independently:

```python
tier1_mappings_by_table = {}
for table_name, rows in parsed_tables.items():
    columns = rows[0].keys()
    table_mappings = []
    
    for column in columns:
        mapping = apply_tier1_strategies(column)
        table_mappings.append(mapping)
    
    tier1_mappings_by_table[table_name] = table_mappings

# Result: Per-table mappings with aggregated stats
total_mapped = sum(len(m) for m in tier1_mappings_by_table.values())
```

### LangGraph Graph Compilation

```python
from langgraph.graph import StateGraph

graph_builder = StateGraph(MigrationState)

# Add nodes
graph_builder.add_node("node_1_ingest", ingest_node)
graph_builder.add_node("node_2_mapper", deterministic_mapper)
graph_builder.add_node("node_3_semantic", semantic_mapper)
graph_builder.add_node("node_4_review", human_review_node)
# ... etc

# Add conditional edges
graph_builder.add_conditional_edges(
    "node_2_mapper",
    lambda state: 3 if state["unresolved_count"] > 0 else 5,
    {3: "node_3_semantic", 5: "node_5_preprocess"}
)

# Compile with PostgreSQL checkpointer
from langgraph.checkpoint.postgres import PostgresSaver
checkpointer = PostgresSaver.from_conn_string(db_url)
graph = graph_builder.compile(checkpointer=checkpointer)
```

---

## 11. What's NOT Implemented

### Planned (v1.2) but Postponed
- **Platform Schema Setup as separate workflow** — Planned for v2.1
  - Would use same 9-node pipeline with `workflow_type: "platform_setup"` flag
  - Fetch Fiix/Maximo schema → map to plenum_cafm → store in `platform_schema_mappings` table
  - Then future uploads use pre-established mapping (skip Nodes 2–4)

### TODOs in Code
- `cleanup_expired_migrations()` in worker.py (marked TODO)
- Fiix debug endpoint (marked REMOVE IN PROD)
- Mapping flow visualization (Node 8, optional)

---

## 12. Performance Characteristics

| Operation | Typical Time | Bottleneck |
|-----------|--------------|------------|
| Node 1: Parse + describe | 3–5 sec | Claude Haiku call (~1–2s) |
| Node 2: 4-strategy mapping | 1–2 sec | Dict/regex lookups <1s, Claude ≤0.5s |
| Node 3: Semantic + embeddings | 2–3 sec | OpenAI embeddings (~1s), cosine sim <1s |
| Node 4: Human review | 30 sec – ∞ | Customer decision time |
| Node 5: Data cleaning | 1–2 sec | Dedup + null handling |
| Node 6: Hierarchy detection | 2–4 sec | Claude Sonnet call (~2–3s) |
| Node 7: Hierarchy verification | 30 sec – ∞ | Customer decision time |
| Node 8: Output generation | 3–5 sec | PDF generation (~2–3s), Azure upload (~1s) |
| Node 9: Final handoff | 1–2 sec | HTTP call to svc-ingestion |
| **Total (no HITL)** | **~15–20 sec** | — |
| **Total (with 3 HITL gates)** | **Minutes to hours** | Customer response |

---

## 13. Testing Coverage

### Testing Endpoints (9)

Each node can be tested independently via `/api/testing/{node_name}` endpoints:

```bash
# Test Node 1 only
curl -X POST http://localhost:8003/api/testing/upload \
  -F "file=@export.xlsx" \
  -F "cmms_name=Fiix" \
  -F "organization_id=00000000-0000-0000-0000-000000000001"

# Test Nodes 1–3 with semantic mapping
curl -X POST http://localhost:8003/api/testing/ingest-with-semantic \
  -F "file=@export.xlsx" \
  -F "cmms_name=Fiix"
  # mapper_json optional — if missing, uses DB default

# Test Node 6 (hierarchy)
curl -X POST http://localhost:8003/api/testing/resolve-hierarchy \
  -H "Content-Type: application/json" \
  -d @node6_payload.json
```

### End-to-End Test

```python
# tests/test_e2e_pipeline.py
async def test_full_9_node_pipeline():
    """Run complete migration without HITL gates"""
    response = await client.post("/api/migration/start", ...)
    migration_id = response.migration_id
    
    # Poll status until complete
    while True:
        status = await client.get(f"/api/migration/{migration_id}/status")
        if status.status == "complete":
            break
        await asyncio.sleep(1)
    
    # Verify outputs
    assert status.output_json_url is not None
    assert status.output_csv_url is not None
    ...
```

---

## 14. Known Limitations & Future Enhancements

### Limitations (v2.0)

1. **No automatic cycle resolution** — Cycles detected but require customer intervention
2. **Platform setup not separate** — Planned for v2.1 as dedicated workflow
3. **Fiix only for testing** — More platforms (Maximo, SAP, Archibus) not yet implemented
4. **No incremental mappings** — Each migration re-does Tier 1 (could cache per org/source)
5. **No mapping learning** — Corrections don't improve future Tier 2/4 prompts (setup complete, just needs feedback loop)

### Planned for v2.1

1. **Platform Setup Workflow** — Separate flow to register Fiix/Maximo schema once
2. **Mapping Learning Loop** — Use LangSmith feedback to refine prompts
3. **Multi-source Support** — More CMMS platforms (SAP, Archibus, Infor EAM, eMaint)
4. **Incremental Mapping** — Reuse per-org/source mappings to skip Tier 1–2 for repeat sources
5. **Real-time Tier 1 Caching** — Cache Tier 1 results in Redis per CMMS field pattern
6. **Mapping Flow UI** — Visual diagram of field mapping decisions

---

## 15. Observability & Debugging

### LangSmith Tracing (Optional)

Set environment variables to enable:
```bash
LANGSMITH_API_KEY=...
LANGSMITH_TRACING=true
```

All 9-node executions automatically traced to LangSmith with:
- Full graph execution trace
- Per-LLM call details (prompt, response, tokens, latency)
- LangGraph interrupt events (pause/resume)
- Per-node metadata (migration_id, CMMS name, field counts)
- Error traces with state snapshots

### Prometheus Metrics

Exposed at `GET /metrics`:
- `migration_jobs_total` (counter, by status)
- `migration_duration_seconds` (histogram)
- `tier1_mapped_fields` (gauge)
- `tier2_semantic_calls` (counter)
- `hierarchy_cycles_detected` (gauge)
- `azure_blob_uploads` (counter, by format)
- `svc_ingestion_handoff_latency` (histogram)
- Standard FastAPI metrics (request count, latency, errors)

### WebSocket Real-time Events

Connect to `ws://localhost:8003/ws/migration/{migration_id}` to stream:
```json
{"event": "node_started", "node": 1, "timestamp": "2026-04-10T10:30:00Z"}
{"event": "field_mapping", "tier": "T1_exact", "source": "ASSETNUM", "target": "asset_code"}
{"event": "gate_interrupt", "gate": 1, "message": "Waiting for user approval"}
{"event": "node_complete", "node": 2, "duration_ms": 1234}
```

### Streamlit UI Integration

Real-time progress display:
```
┌─────────────────────────────────────┐
│ Migration: MOB-ASSETS-2026-04-10    │
├─────────────────────────────────────┤
│ Status: Node 3 (Semantic Mapping)   │
│ Progress: ▓▓▓▓▓░░░░ 50%             │
│                                     │
│ Node 1 ✅ Parse (3.2s)              │
│ Node 2 ✅ Mapping (1.1s)            │
│ Node 3 ⧖ Semantic (1.8s elapsed)    │
│ Node 4 ⏸ Review                      │
│ Node 5 ⏳ Waiting                     │
│                                     │
│ Statistics:                         │
│  • T1 mapped: 43 / 47 (91%)         │
│  • T2 semantic: 2 / 4               │
│  • Overall confidence: 0.87         │
│  • Est. remaining: 2 min            │
│                                     │
└─────────────────────────────────────┘
```

---

## 16. Deployment & Ops

### Docker

```bash
# Build
docker build -t plenum/svc-ai-schema-mapper:latest .

# Run (via docker-compose)
docker-compose up svc-ai-schema-mapper
```

### Environment Setup

```bash
# .env file (repo root)
ANTHROPIC_API_KEY=sk-...
OPENAI_API_KEY=sk-...
DB_URL=postgresql+asyncpg://...@postgres:5432/cafm_connectors
REDIS_URL=redis://redis:6379/0
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=...
SVC_INGESTION_URL=http://svc-ingestion:8001
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
```

### Database Migrations

```bash
# Apply migration 003 (create 4 tables)
cd alembic
alembic upgrade head
```

### Service Dependencies

- PostgreSQL (5432) — Shared with other services
- Redis (6379) — Shared cache/queue (optional)
- OpenAI API — For embeddings
- Azure Blob Storage — For output files
- svc-ingestion (8001) — Final handoff target

---

## 17. Quick Start

### Local Development

```bash
# Install
pip install -r requirements.txt

# Set up .env
cp .env.example .env
# Edit .env with your API keys

# Start database & dependencies
docker-compose up postgres redis

# Run service
uvicorn src.app:app --host 0.0.0.0 --port 8003 --reload

# Test
curl http://localhost:8003/health
```

### First Migration

```bash
# Upload CMMS export
curl -X POST http://localhost:8003/api/migration/start \
  -F "file=@assets.xlsx" \
  -F "cmms_name=Fiix" \
  -F "organization_id=12345678-1234-1234-1234-123456789abc"

# Check status
curl http://localhost:8003/api/migration/{migration_id}/status

# If GATE 1, approve mappings
curl -X POST http://localhost:8003/api/migration/{migration_id}/approve \
  -H "Content-Type: application/json" \
  -d '{
    "decisions": [
      {"source_field": "RESERVED_FOR", "target_field": "location_code", "action": "accept"}
    ]
  }'

# Download JSON output
curl http://localhost:8003/api/migration/{migration_id}/download/json
```

---

## Conclusion

`svc-AI-Schema-Mapper` v2.0 is a **production-ready, universal CMMS data migration service** with:

✅ **9-node LangGraph pipeline** (Node 1–9, fully implemented)  
✅ **3 HITL gates** for customer approval (Nodes 4, 7, 9)  
✅ **4-tier deterministic + semantic mapping** (Tier 1: 4 strategies, Tier 2: embeddings)  
✅ **Multi-table support** (per-table validation, metrics, cleaning)  
✅ **Hierarchy detection** (Claude Sonnet FK inference + cycle detection)  
✅ **4 output formats** (JSON, CSV, SQL, PDF)  
✅ **28 API endpoints** (migration + testing + mapping templates + platform integration)  
✅ **PostgreSQL persistence** (checkpointer for state resumability)  
✅ **Real-time WebSocket events** (for Streamlit UI)  
✅ **LangSmith observability** (full trace per migration)  
✅ **Dynamic schema introspection** (replaces hardcoded canonical fields)  

**Ready for:** Production CMMS migrations (Fiix, with extensibility for Maximo/SAP/Archibus)
