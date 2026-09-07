# Schema Mapping Pipeline — 6-Node LangGraph

**Separate flow** from data ingestion pipeline (9 nodes). Maps external CMMS schema → canonical plenum_cafm schema.

## High-Level Flow

```
External CMMS Schema Definition
    ↓
[Node 1] Ingest Schema
    Read table/column definitions from DB, YAML, JSON, or DDL
    ↓
[Node 2] Deterministic Mapping
    4-tier strategy: exact → aliases → regex → Haiku constrained
    ↓
[Node 3] Semantic Mapping
    Embedding-based similarity for unresolved columns
    ↓
[Node 4] FK & Hierarchy Detection
    Detect foreign key relationships and table hierarchy
    ↓
[Node 5] Verify Hierarchy ⏸ HITL GATE
    User reviews/approves detected relationships
    ↓
[Node 6] Output Generation
    Generate final JsonMapperConfig for use in data ingestion
    ↓
User gets mapping config + can start data ingestion
```

---

## Node Specifications

### Node 1: Ingest Schema

**Purpose:** Read and parse external schema definition

**Input:** `SchemaMappingState` with:
- `schema_source`: "database_url" | "yaml_file" | "json_file" | "ddl_sql"
- `schema_format`: "sql" | "yaml" | "json"
- Connection details or file content

**Output:** Update state with:
- `external_tables`: dict[table_name] → SchemaTableInfo
- `table_count`: int
- `total_columns`: int
- `schema_summary`: str

**Responsibilities:**
1. Parse schema from source
2. Extract table names, column names, data types, FK relationships
3. Build column metadata (nullable, PK, FK info, comments)
4. Generate human-readable summary

**Error Handling:**
- Invalid connection string → error_message
- Unparseable YAML/JSON → error_message
- DB introspection fails → error_message

**Status:** `ingest` → `mapping`

---

### Node 2: Deterministic Mapping

**Purpose:** Map external columns → canonical fields using 4-tier strategy

**Input:** State with `external_tables`

**Output:** Update state with:
- `tier1_mappings`: list[CanonicalFieldMapping]
- `tier1_mapped_count`: int
- `unmapped_after_t1`: list[SchemaMappingFieldInfo]

**Strategies (in order):**

1. **T1 Exact Match** (confidence: 0.99)
   - `asset_code` → `asset_code`

2. **T1 Alias Lookup** (confidence: 0.95–0.98)
   - Use `CMMS_ALIASES` mapping
   - `assetnum` → `asset_code`

3. **T1 Regex Pattern** (confidence: 0.90–0.94)
   - Match field names against patterns
   - `^ASSET_.*` → suggests `asset_` prefix matches

4. **T1 Haiku Constrained** (confidence: 0.85–0.92)
   - Only if previous 3 strategies didn't resolve
   - LLM with context: "Map this column to canonical schema"
   - Constrained to choose from canonical fields only

**Thresholds:**
- ≥ 0.85 confidence → proceed to next node
- < 0.85 confidence → pass to Node 3 (semantic mapping)

**Status:** `mapping` → `semantic`

---

### Node 3: Semantic Mapping

**Purpose:** Use embeddings to match unresolved columns

**Input:** State with `unmapped_after_t1`

**Output:** Update state with:
- `tier2_auto_mapped`: list[CanonicalFieldMapping]  (≥ 0.85)
- `tier2_flagged`: list[CanonicalFieldMapping]  (0.65–0.84)
- `tier2_unmappable`: list[SchemaMappingFieldInfo]  (< 0.65)
- `overall_mapping_confidence`: float

**Strategy:**
1. Embed unresolved field names + descriptions
2. Compute cosine similarity vs cached canonical field embeddings
3. Score results (0.0–1.0)
4. Categorize:
   - ≥ 0.85: auto-map (confidence high)
   - 0.65–0.84: flag for review (need human check)
   - < 0.65: unmappable (keep in raw_metadata)

**Embeddings:** Use existing OpenAI text-embedding-3-small (same as semantic mapper uses)

**Audit:** Log all 3 scores + top candidates for tier2_flagged

**Status:** `semantic` → `hierarchy`

---

### Node 4: FK & Hierarchy Detection

**Purpose:** Detect foreign key relationships and implicit hierarchies

**Input:** State with complete mapping

**Output:** Update state with:
- `detected_foreign_keys`: list[ForeignKeyDetection]
- `detected_hierarchy`: HierarchyNode (tree structure)
- `implicit_hierarchies`: dict[str, Any]  (SAP-style code hierarchies)
- `hierarchy_cycles`: list[list[str]]  (if cycles detected)

**FK Detection:**
1. Look for columns with "id", "code" patterns
2. Cross-reference column names: if table_a has `parent_table_b_id` → likely FK
3. Check data if available (first 100 rows)
4. Use Haiku to classify relationship type: REFERENCE, CONTAINMENT, OWNERSHIP, PART_OF

**Hierarchy Building:**
1. Start with tables that have no FK pointing to them (root level)
2. Build tree: root → children → grandchildren
3. Example: `organizations` → `locations` → `assets` → `work_orders`

**Implicit Hierarchies:**
- Detect code hierarchies (e.g., SAP: "LEVEL_CODE-LEVEL_CODE-LEVEL_CODE")
- Mark as implicit for user review

**Status:** `hierarchy` → `verify_hierarchy`

---

### Node 5: Verify Hierarchy ⏸ HITL GATE 1

**Purpose:** Human review and approval of detected FK/hierarchy

**Input:** State with detected relationships + hierarchy tree

**Output:** Update state with:
- `hierarchy_review_payload`: dict for user (what they see)
- `user_hierarchy_corrections`: dict (what they submitted)
- `hierarchy_approved`: bool
- `hierarchy_approved_at`: datetime

**What User Sees:**
1. Detected foreign keys (source table, column → target table, column)
2. Confidence scores + reasoning for each FK
3. Hierarchy tree visualization
4. Any detected cycles (needs correction)
5. Implicit hierarchies (for confirmation)

**User Actions:**
1. Approve all detected FKs
2. Reject specific FKs
3. Manually add missing FKs
4. Correct hierarchy ordering

**Interrupt Mechanism:**
- Use LangGraph `interrupt()` to pause execution
- Wait for user input via `/api/schema-mapping/{id}/verify-hierarchy` endpoint
- Resume with corrected state

**Status:** `verify_hierarchy` → `output` (or back to `hierarchy` if corrections needed)

---

### Node 6: Output Generation

**Purpose:** Generate final mapping config (JsonMapperConfig-compatible)

**Input:** State with approved hierarchy

**Output:** Update state with:
- `final_mapping_config`: dict (JsonMapperConfig structure)
- `extra_fields_config`: list[ExtraFieldConfig]
- `status`: `complete`

**Output Structure:**
```json
{
  "version": "1.0",
  "source_system": "Maximo",
  "canonical_fields": {
    "asset_code": "Unique identifier for assets",
    "asset_name": "Human-readable name",
    ...
  },
  "vendor_aliases": {
    "asset_code": ["assetnum", "assetid", "xassets", ...],
    "asset_name": ["assetname", "xasset_name", ...],
    ...
  },
  "hierarchies": {
    "organizational": {
      "root": "organizations",
      "levels": ["organizations", "locations", "assets"]
    }
  }
}
```

**Extra Fields Handling:**
- User decides per unmapped field:
  - Store in `raw_metadata.{field_name}`
  - Create custom column in universal schema
  - Skip (don't ingest)

**Audit:**
- Total fields: X
- Mapped: Y (coverage: Y/X)
- Unmapped: Z
- Hierarchy confidence: 0.XX
- FK accuracy: 0.XX

**Status:** `output` → `complete`

---

## State Flow

```
START → [Ingest] → [Deterministic] → [Semantic] → [FK/Hierarchy] → [Verify*] → [Output] → END
         ↓          ↓                ↓            ↓                 ↓           ↓
      ingest      mapping          semantic     hierarchy      verify_       output
      status      status           status       status         hierarchy     status
                                                                status
                                                                (interrupt)
```

---

## Key Differences from Data Ingestion Pipeline

| Aspect | Data Ingestion (9 nodes) | Schema Mapping (6 nodes) |
|--------|--------------------------|------------------------|
| **Input** | Actual data (rows) | Schema definitions (columns) |
| **Preprocessing** | Needed (dedup, nulls, coerce) | Not needed |
| **Focus** | Data values | Column definitions |
| **Hierarchy** | Inferred from data patterns | From FK relationships |
| **Output** | IntermediateSchema + data rows | JsonMapperConfig only |
| **Use Case** | Ingest customer data | Configure before data ingestion |

---

## API Endpoints

### Start Schema Mapping
```
POST /api/schema-mapping/start
Body: StartSchemaMappingRequest
Returns: SchemaMappingProgressResponse
```

### Get Progress
```
GET /api/schema-mapping/{id}/status
Returns: SchemaMappingProgressResponse
```

### Get Mapping Results (after Node 3)
```
GET /api/schema-mapping/{id}/mapping-results
Returns: MappingResultResponse
```

### Approve Hierarchy (Node 5 resumption)
```
POST /api/schema-mapping/{id}/verify-hierarchy
Body: HierarchyApprovalRequest
Returns: HierarchyApprovalResponse
```

### Get Final Config (after Node 6)
```
GET /api/schema-mapping/{id}/final-config
Returns: FinalMappingConfigResponse
```

---

## Error Handling

**Node-level errors:**
- Ingest: DB connection failed, file parse error
- Deterministic: Schema introspection failed
- Semantic: Embedding API down
- FK/Hierarchy: Circular dependency detected (will show to user for resolution)
- Verify Hierarchy: User makes contradictory corrections (validation)
- Output: Extra field config invalid (validation)

**Recovery:**
- Persist state to DB after each node (like existing pipeline)
- Allow resume from checkpoint if node fails
- Provide clear error message to user

---

## Next Steps (Implementation Order)

1. ✅ Define state (`schema_state.py`)
2. ✅ Define schemas (`schemas_schema_mapping.py`)
3. Create `schema_mapping_graph.py` - compile nodes into graph
4. Implement Node 1: `nodes/schema_ingest_node.py`
5. Implement Node 2: `nodes/schema_deterministic_node.py`
6. Implement Node 3: `nodes/schema_semantic_node.py`
7. Implement Node 4: `nodes/schema_hierarchy_node.py`
8. Implement Node 5: `nodes/schema_verify_hierarchy_node.py`
9. Implement Node 6: `nodes/schema_output_node.py`
10. Add API endpoints to `app.py`
11. Add tests

---

## Database Changes

Need to track schema mapping sessions and results:

```sql
CREATE TABLE schema_mappings (
    id UUID PRIMARY KEY,
    organization_id UUID,
    external_cmms_name VARCHAR(100),
    status VARCHAR(50),  -- ingest | mapping | semantic | hierarchy | complete
    created_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    state_json JSONB,  -- Full SchemaMappingState serialized
    final_mapping_config JSONB,  -- FinalMappingConfigResponse
    created_by UUID,
    notes TEXT
);

CREATE TABLE schema_mapping_audit (
    id UUID PRIMARY KEY,
    schema_mapping_id UUID REFERENCES schema_mappings(id),
    node_name VARCHAR(50),
    event_type VARCHAR(50),  -- started | completed | error | user_input
    details JSONB,
    created_at TIMESTAMPTZ
);
```

---

## Integration with Data Ingestion

**After schema mapping is complete:**

1. User gets `final_mapping_config` JSON
2. User calls data ingestion endpoint with this config as `mapper_json`
3. Data ingestion pipeline uses it for field mapping (Node 2)
4. No need for user to manually create JSON mapper config

**Benefits:**
- Users don't need to understand JsonMapperConfig format
- Schema validation happens upfront before data ingestion
- FK relationships understood before data arrives
- Higher data quality due to proper schema understanding
