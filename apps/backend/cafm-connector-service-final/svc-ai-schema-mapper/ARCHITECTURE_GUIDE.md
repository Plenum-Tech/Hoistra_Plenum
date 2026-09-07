# svc-AI-Schema-Mapper — Complete Architecture Guide

*A beginner-friendly walkthrough of the 9-node pipeline that converts any customer CMMS export into a validated, standardized format.*

---

## 🎯 The Big Picture Problem

Your company has **10+ different CMMS systems** (Maximo, Fiix, SAP, Archibus, etc.) in different organizations. Each has its own column names, data formats, and structure. You need a **universal translator** that converts any customer's export into a standard format that can be ingested into your unified database.

**The Challenge:**
- Company A exports `Asset_ID`, Company B uses `EQUIP#`, Company C uses `AssetCode`
- They all mean the same thing but have different names
- Manual mapping would take weeks per customer
- You need an **automated, AI-powered solution** that validates every step

**Enter: svc-ai-schema-mapper** — A 9-node pipeline that automatically maps, validates, detects relationships, and prepares data for handoff.

---

## 🔄 The Complete Flow (Visual)

```
Customer CMMS Export (CSV/Excel)
         ↓
    ┌────────────────────────────────────────┐
    │   svc-AI-Schema-Mapper (9 Nodes)       │
    │   Streamlit UI for testing             │
    └────────────────────────────────────────┘
         ↓
    [Node 1] File Upload & Detection
         ↓ (EL-M.1 validation)
    [Node 2] Deterministic Mapping (4 strategies)
         ↓ (EL-M.2 validation)
    [Node 3] Semantic Mapping (AI embeddings)
         ↓ (EL-M.3 validation)
    ⏸ [Node 4] GATE 1 — Human Review
         ↓ (EL-M.4 validation)
    [Node 5] Data Cleaning & Preprocessing
         ↓ (EL-M.5 validation)
    [Node 6] Hierarchy Detection (FK relationships)
         ↓ (EL-M.6 validation)
    ⏸ [Node 7] GATE 2 — Hierarchy Verification
         ↓ (EL-M.7 validation)
    [Node 8] Output Generation (JSON/CSV/SQL/PDF)
         ↓ (EL-M.8 validation)
    ⏸ [Node 9] GATE 3 — Final Approval & Handoff
         ↓ (EL-M.9 validation)
    svc-ingestion (next stage)
```

---

## 📋 Node-by-Node Breakdown

### **Node 1: Ingest & Configure** 🔍

**What it does:**
- Accepts a CSV or Excel file from the customer
- Figures out **what's inside** the file

**Step-by-step:**
1. **Detect encoding** — Is it UTF-8? Latin-1? (matters for special characters)
2. **Detect delimiter** — Is it comma (,), tab (\t), or semicolon (;)?
3. **Parse the file** — Read all rows and columns
4. **Auto-detect data types** — Which columns are numbers? Text? Dates?
5. **Describe the dataset** — Call Claude Haiku to read the column names and give semantic descriptions
6. **Calculate completeness** — How much data is missing (null values)?

**Real example:**
```
Input file: assets.csv
- Encoding: UTF-8 ✓
- Delimiter: Comma (,) ✓
- Tables detected: 1 (assets)
- Rows: 60
- Columns: 15
- Completeness: 94% (some null serial numbers)
```

**Validation (EL-M.1):**
- ✅ If: has rows > 0 AND columns > 0
- ❌ If: empty file or unreadable

**Output stored in state:**
- `parsed_tables`: Raw data from the file
- `table_names`: List of tables ("assets", "work_orders", etc.)
- `column_descriptions`: AI-generated semantic meaning for each column

---

### **Node 2: Deterministic Mapping** 🔗

**What it does:**
- Maps customer's column names → canonical CAFM column names
- Uses **4 strategies** in sequence, each getting smarter

**The 4 Strategies (in order):**

**Strategy 1: Exact Match** (confidence: 0.99)
```
Customer column: "asset_code"
Canonical field: "asset_code"
Result: ✅ MATCH! Confidence 99%
```

**Strategy 2: Alias Lookup** (confidence: 0.95-0.98)
```
Customer column: "EQUIP#" (SAP naming)
Alias table says: EQUIP# = asset_code
Result: ✅ MATCH! Confidence 97%
```

**Strategy 3: Regex Pattern** (confidence: 0.90-0.94)
```
Customer column: "asset_id_code"
Pattern: ^asset.*code$ → matches "asset_code" pattern
Result: ✅ MATCH! Confidence 91%
```

**Strategy 4: Claude Haiku LLM** (confidence: 0.85-0.92)
```
Customer column: "xEquipmentIdentifier"
Haiku sees: "x" prefix = Fiix system, "Equipment" = asset
Result: ✅ MATCH! Confidence 88%
```

**Real example with multiple columns:**
```
Input columns:        Mapped to:           Confidence:
- asset_code    →    asset_code          99% (exact)
- EQUIP_STATUS  →    status              97% (alias)
- weird_field_x →    ??? (no match)      UNRESOLVED
```

**Validation (EL-M.2):**
- ✅ If: overall mapping confidence ≥ 0.80
- ❌ If: too many low-confidence or duplicate target fields

**Output stored in state:**
- `tier1_mappings_by_table`: All successful mappings with confidence scores
- `unresolved_by_table`: Fields that didn't match any strategy

---

### **Node 3: Semantic Mapping** ✨

**What it does:**
- Takes fields that Node 2 couldn't map
- Uses **embedding similarity** to find the closest match
- This is the AI magic step

**How it works:**

1. **Embed all canonical fields** — Convert each target field into a number vector using OpenAI embeddings
   - "asset_code" → [0.23, -0.11, 0.44, ...] (vector of 1536 numbers)
   - "asset_name" → [0.19, -0.05, 0.51, ...]

2. **Embed unresolved customer fields** — Do the same for fields that didn't match
   - "weird_field_x" → [0.21, -0.09, 0.48, ...]

3. **Calculate similarity** — Compare vectors using cosine similarity
   - If vectors are similar → they probably mean the same thing
   - 1.0 = identical, 0.0 = completely different

**Real example:**
```
Unresolved field: "equipment_location_code"
Canonical fields being compared:
- "location_code":     similarity = 0.92 ← BEST MATCH!
- "asset_code":        similarity = 0.45
- "maintenance_area":  similarity = 0.38

Result: Map to "location_code" with confidence 92%
```

**Confidence Thresholds:**
- ✅ Score ≥ 0.85 → **Auto-accept** (high confidence)
- 🟡 Score 0.65-0.85 → **Flag for human review** (medium confidence)
- ❌ Score < 0.65 → **Mark unmappable** (too different)

**Validation (EL-M.3):**
- ✅ If: all fields now have a mapping (or explicitly unmappable)
- 🟡 If: many medium-confidence fields → triggers **GATE 1** for human review

**Output stored in state:**
- `tier2_auto_by_table`: Auto-accepted mappings (≥0.85 confidence)
- `tier2_flagged_by_table`: Medium-confidence mappings flagged for review
- `tier2_unmappable_by_table`: Couldn't map this field

---

### **Node 4: Human Review (GATE 1)** 👤 ⏸️

**What it does:**
- **PAUSES** the pipeline
- Shows human reviewer the medium-confidence mappings
- Waits for approval/correction
- Resumes after decision

**The Interrupt:**
```
GATE 1 REVIEW FORM
==================
These fields need your attention:

Field: "weird_field_x"
AI suggested: "maintenance_area" (confidence: 0.73)
You can:
  ✅ Approve suggestion
  ❌ Reject and mark unmappable
  ✏️  Override with a different field

Field: "cost_center"
AI suggested: "vendor_id" (confidence: 0.68)
You can:
  ✅ Approve
  ❌ Reject
  ✏️  Override
```

**What happens on resume:**
- Customer approves/rejects each suggestion
- Approved mappings added to `tier2_human_decisions_by_table`
- Rejected fields marked as unmappable
- Pipeline resumes to Node 5

**Validation (EL-M.4):**
- ✅ If: human has approved/rejected all flagged fields
- ✅ If: no duplicate target fields after decisions
- ❌ If: human made invalid choice (field doesn't exist)

**Example outcome:**
```
Before review:
- "weird_field_x" → ? (flagged)
- "cost_center" → ? (flagged)

Human decisions:
- "weird_field_x" → REJECT (unmappable)
- "cost_center" → APPROVE as vendor_id

Result stored in state:
- tier2_human_decisions_by_table["assets"][0] = {source: "cost_center", target: "vendor_id", tier: "T2_human"}
```

---

### **Node 5: Preprocess & Validate** 🧹

**What it does:**
- Cleans the data before it goes into the database
- Removes duplicates, handles missing values, fixes dates

**4 cleaning steps:**

**Step 1: Deduplication**
```
Input rows:
- Asset 001, Location A, active
- Asset 001, Location A, active     ← DUPLICATE
- Asset 002, Location B, active

Output rows:
- Asset 001, Location A, active     (duplicate removed)
- Asset 002, Location B, active

Metric: Dropped 1 duplicate
```

**Step 2: Null Handling**
```
Numeric columns (like quantity):
  NULL → 0 (no parts = 0 parts)

Text columns (like description):
  NULL → "" (empty string)

Date columns:
  NULL → LEFT AS NULL (unknown date is unknown)
```

**Step 3: Date Coercion**
```
Input dates in various formats:
- 2025-12-31
- 31/12/2025
- 12/31/2025

Normalized to:
- 2025-12-31 (ISO 8601 standard)
```

**Step 4: Foreign Key Pre-check**
```
Looking for columns that reference other tables:
- "asset_code" in work_orders table
- Check: do these values exist in assets table?
- Result: 95% match (5 orphaned records found)
```

**Validation (EL-M.5):**
- ✅ If: post-dedup rows ≥ 80% of original rows
- ✅ If: all date coercions successful
- ❌ If: lost too much data during dedup

**Output stored in state:**
```
- cleaned_tables: Deduplicated, cleaned data per table
- row_count_post_dedup_by_table: {"assets": 57, "work_orders": 74}
- dedup_drop_count_by_table: {"assets": 3, "work_orders": 0}
- data_quality_warnings: ["Assets: Dropped 3 duplicates", "WOs: 5 null descriptions"]
```

---

### **Node 6: Resolve Hierarchy** 🌳

**What it does:**
- Detects **relationships between tables**
- Figures out which tables contain which other tables
- Example: Sites contain Locations, which contain Assets

**The relationships it finds:**

**Foreign Key (FK) Detection:**
```
Table: work_orders
Field: asset_code

Does this exist in assets table?
- asset_code = "MOB-001" exists in assets ✓
- asset_code = "MOB-002" exists in assets ✓
- asset_code = "UNKNOWN" does NOT exist ✗

Result: 99% match rate → confirmed FK relationship!
Relationship: work_orders.asset_code → assets.asset_code
```

**Implicit Hierarchy (SAP-style codes):**
```
Column: location_code
Values:
- "SITE-001-LOC-A"
- "SITE-001-LOC-B"
- "SITE-002-LOC-A"

Pattern detected: SITE → LOCATION structure
Result: Extract implicit 2-level hierarchy
```

**Cycle Detection (DFS algorithm):**
```
Relationships found:
- Assets → Locations
- Locations → Sites
- Sites → Assets ← CYCLE!

Result: ❌ CYCLE DETECTED
Action: Flag for human review
```

**Validation (EL-M.6):**
- ✅ If: no cycles in FK relationships
- ✅ If: data_match_rate ≥ 80% for confirmed FKs
- ❌ If: cycles detected

**Output stored in state:**
```
- confirmed_hierarchies: [
    {source_table: "work_orders", source_column: "asset_code",
     target_table: "assets", target_column: "asset_code",
     confidence: 0.99, data_match_rate: 0.99},
    ...
  ]
- hierarchy_cycles: [] (empty = no cycles!)
- containment_hierarchy_by_table: {"assets": {children: [work_orders]}}
```

---

### **Node 7: Verify Hierarchy (GATE 2)** 🌳 ⏸️

**What it does:**
- **PAUSES** the pipeline
- Shows customer the detected hierarchies
- Asks: "Is this relationship correct?"
- Waits for confirmation/correction

**The Interrupt:**
```
GATE 2 HIERARCHY REVIEW
=======================
We detected these relationships:

Relationship 1:
  work_orders.asset_code → assets.asset_code
  Confidence: 99%
  Match Rate: 99% (395 of 399 WOs found matching asset)
  Type: REFERENCE (each WO belongs to one asset)
  Customer Confirm: ✅ Yes ❌ No ✏️ Correct

Relationship 2:
  assets.parent_asset_id → assets.asset_code
  Confidence: 87%
  Type: SELF_REFERENCE (asset contains sub-assets)
  Customer Confirm: ✅ Yes ❌ No ✏️ Correct

[Show visual tree diagram]
```

**What happens on resume:**
- Customer confirms or corrects relationships
- Corrections added to `confirmed_hierarchies`
- Pipeline resumes to Node 8

**Validation (EL-M.7):**
- ✅ If: all hierarchies customer-confirmed
- ✅ If: no cycles after corrections
- ❌ If: unresolved cycles remain

**Example outcome:**
```
Before review:
- work_orders → assets (detected)
- assets.parent_asset_id → assets (detected, may have cycle)

Customer confirms:
- work_orders → assets ✅ CORRECT
- assets.parent_asset_id → assets ✅ CORRECT (no cycle found)

Result: Confirmed hierarchies ready for output
```

---

### **Node 8: Output Generation** 📤

**What it does:**
- Takes all cleaned, mapped, validated data
- Produces **4 export formats**
- Uploads everything to Azure Blob Storage
- Builds the final `IntermediateSchema`

**4 Export Formats:**

**1. Nested JSON** (hierarchical structure)
```json
{
  "sites": [
    {
      "name": "Dubai HQ",
      "locations": [
        {
          "name": "Building A",
          "assets": [
            {
              "asset_code": "MOB-001",
              "name": "Air Handler Unit",
              "work_orders": [
                {
                  "wo_code": "WO-2025-001",
                  "priority": "High"
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

**2. Flat CSV** (one row per record)
```
asset_code,asset_name,location_code,status,wo_code,wo_priority
MOB-001,Air Handler,LOC-A,active,WO-2025-001,High
MOB-001,Air Handler,LOC-A,active,WO-2025-002,Medium
MOB-002,Chiller,LOC-B,active,WO-2025-003,High
```

**3. SQL INSERT Statements** (ready to load into DB)
```sql
INSERT INTO plenum_cafm.assets (asset_code, asset_name, location_code, status) 
  VALUES ('MOB-001', 'Air Handler', 'LOC-A', 'active');
INSERT INTO plenum_cafm.work_orders (wo_code, asset_code, priority) 
  VALUES ('WO-2025-001', 'MOB-001', 'High');
```

**4. PDF Report** (human-readable summary)
```
MIGRATION SUMMARY
=================
File: assets_maximo_export.csv
Date: 2025-04-06
Status: ✅ SUCCESS

TIER BREAKDOWN:
- Tier 1 (Exact): 12 fields
- Tier 2 (Semantic): 3 fields
- Tier 2 (Human): 2 fields
- Unmapped: 0 fields

DATA QUALITY:
- Assets: 60 records (58 after dedup)
- Work Orders: 74 records
- Completeness: 94%

HIERARCHIES DETECTED:
- work_orders → assets (99% match)
```

**Upload to Azure Blob:**
```
migrations/{migration_id}/
  ├── output.json         ← Nested structure
  ├── output.csv          ← Flat export
  ├── INSERT.sql          ← SQL statements
  └── report.pdf          ← Summary report
```

**Build IntermediateSchema:**
```python
{
  "ingestion_id": "uuid-12345",
  "source_type": "csv",
  "agent_id": "schema-mapper",
  "source_filename": "assets_maximo_export.csv",
  "extracted_at": "2025-04-06T10:30:00Z",
  "entities": {
    "assets": [...60 cleaned records...],
    "work_orders": [...74 cleaned records...],
    "parts": [...],
    "scheduled_pm": [...]
  },
  "confidence": {
    "overall": "high",
    "eval_score": 0.94
  }
}
```

**Validation (EL-M.8):**
- ✅ If: IntermediateSchema has all required fields
- ✅ If: all entity records valid
- ❌ If: missing required top-level keys

**Output stored in state:**
```
- intermediate_schema: The full schema dict
- output_json_url: "blob://container/migrations/{id}/output.json"
- output_csv_url: "blob://container/migrations/{id}/output.csv"
- output_sql_url: "blob://container/migrations/{id}/INSERT.sql"
- migration_report_url: "blob://container/migrations/{id}/report.pdf"
```

---

### **Node 9: Write to Platform (GATE 3)** ✍️ ⏸️

**What it does:**
- **FINAL GATE** — Last chance to review before handoff
- Shows a summary of what's being sent
- Waits for customer approval
- POSTs IntermediateSchema to svc-ingestion
- Marks migration as complete

**The Interrupt:**
```
GATE 3 FINAL CONFIRMATION
==========================
Ready to send to svc-ingestion?

SUMMARY:
- Source: assets_maximo_export.csv
- Confidence: 94%
- Assets: 60
- Work Orders: 74
- Parts: 38
- Total Entities: 172

Files being sent:
✓ output.json
✓ output.csv
✓ INSERT.sql
✓ report.pdf

ACTION:
[ ✅ CONFIRM ]  [ ❌ REJECT ]
```

**What happens on confirm:**
```
1. POST IntermediateSchema to svc-ingestion/api/ingest
2. Wait for response (200/202 = accepted)
3. svc-ingestion returns: {status: "queued", ingestion_id: "..."}
4. Update migration_jobs table in database:
   - status = "complete"
   - completed_at = now
   - output URLs stored
   - progress = 100%
5. Pipeline finishes ✅
```

**What happens on reject:**
```
1. Don't POST to svc-ingestion
2. Mark handoff_status = "rejected"
3. Stop pipeline
4. Wait for user to fix issues and restart
```

**Validation (EL-M.9):**
- ✅ If: svc-ingestion accepts (HTTP 200/202)
- ✅ If: migration_jobs updated successfully
- ❌ If: svc-ingestion rejects or network error

**Output stored in state:**
```
- handoff_status: "sent" (confirmed and transmitted)
- svc_ingestion_response: {status: "queued", ingestion_id: "..."}
- status: "complete"
- completed_at: 2025-04-06T10:45:00Z
```

---

## 🛡️ The Evaluation Layer (Quality Gates)

Each node has a validation checkpoint. If validation fails, the pipeline stops.

| Node | Gate | What it checks | Fails if... |
|------|------|---|---|
| 1 | EL-M.1 | File readable | 0 rows or 0 columns |
| 2 | EL-M.2 | Mapping quality | Overall confidence < 80% |
| 3 | EL-M.3 | Semantic matching | Too many medium-confidence fields |
| 4 | EL-M.4 | Human decisions valid | Invalid selections |
| 5 | EL-M.5 | Data loss acceptable | Lost > 20% of rows in dedup |
| 6 | EL-M.6 | No cycles | Circular FK relationships |
| 7 | EL-M.7 | Hierarchies confirmed | Unresolved cycles |
| 8 | EL-M.8 | Schema valid | Missing required fields |
| 9 | EL-M.9 | Handoff successful | svc-ingestion rejects |

---

## 🎬 Real-World Example: Complete Flow

Let's trace through a real migration:

**INPUT:** Customer uploads `assets_maximo_export.csv`

**NODE 1 — Ingest**
```
✅ File detected: CSV, UTF-8, 60 rows, 15 columns
✅ Tables found: assets
✅ EL-M.1: PASSED
```

**NODE 2 — Deterministic Mapping**
```
Column "asset_code" → Strategy 1 exact match → 99% confidence ✅
Column "EQUIP_LOCATION" → Strategy 2 alias → 97% confidence ✅
Column "weird_field_x" → No match → UNRESOLVED
Overall: 14/15 fields mapped (93%)
✅ EL-M.2: PASSED (≥80%)
```

**NODE 3 — Semantic Mapping**
```
Field "weird_field_x" + context → embedding comparison
Top match: "maintenance_area" (0.82 similarity)
✅ EL-M.3: PASSED
```

**NODE 4 — Human Review (GATE 1)**
```
⏸ PAUSED: "weird_field_x" → "maintenance_area" (0.82 confidence)
👤 Human: "✅ Approve, that's correct"
✅ EL-M.4: PASSED
```

**NODE 5 — Preprocess**
```
Dedup: 60 rows → 58 rows (2 duplicates removed)
Nulls: 12 description fields → ""
Dates: 3 different formats → ISO 8601
✅ EL-M.5: PASSED (58/60 = 96% > 80%)
```

**NODE 6 — Hierarchy**
```
Detected: work_orders.asset_code → assets.asset_code (99% match)
No cycles found
✅ EL-M.6: PASSED
```

**NODE 7 — Hierarchy Verify (GATE 2)**
```
⏸ PAUSED: Show detected relationships
👤 Human: "✅ Correct, work orders do reference assets"
✅ EL-M.7: PASSED
```

**NODE 8 — Output Generation**
```
✅ Generated nested JSON (60 assets, 74 work orders)
✅ Generated flat CSV (combined export)
✅ Generated SQL INSERT statements
✅ Generated PDF report
✅ Uploaded all to Azure Blob
✅ Built IntermediateSchema
✅ EL-M.8: PASSED
```

**NODE 9 — Write (GATE 3)**
```
⏸ PAUSED: Final confirmation needed
Summary: 60 assets, 74 WOs, 94% quality
👤 Human: "✅ CONFIRM, send to svc-ingestion"
📤 POST to svc-ingestion/api/ingest
✅ Response: {status: "queued", ingestion_id: "..."}
✅ EL-M.9: PASSED
✅ MIGRATION COMPLETE
```

---

## 📊 Dashboard View in Streamlit

When you run the pipeline:

```
┌─────────────────────────────────────────────────────────┐
│  🔬 Details | 🔗 Mapping | ✨ Semantic | 👤 Review    │
│  🧹 Preprocess | 🌳 Hierarchy | 📤 Output | ✍️ Write   │
└─────────────────────────────────────────────────────────┘

Tab 1: Details
- File info, encoding, delimiter detected
- Table breakdown
- Completeness metrics

Tab 2: Mapping (Node 2)
- All mapped fields with confidence scores
- Strategy used (exact, alias, regex, LLM)

Tab 3: Semantic (Node 3)
- Medium/low confidence fields with embeddings
- Suggested corrections

Tab 4: Human Review (Node 4)
- Form with flagged mappings
- Approve/reject/override options

Tab 5: Preprocess (Node 5)
- Duplicates removed count
- Null handling summary
- Date coercions

Tab 6: Hierarchy (Node 6)
- Visual tree of detected relationships
- FK match rates
- Cycles detected/resolved

Tab 7: Output (Node 8)
- Links to 4 export files (JSON, CSV, SQL, PDF)
- IntermediateSchema entity counts

Tab 8: Write (Node 9)
- Handoff status
- svc-ingestion response
- Migration complete confirmation
```

---

## 🔄 What Happens After Node 9?

Once Node 9 completes and IntermediateSchema is sent to svc-ingestion:

```
svc-ingestion receives IntermediateSchema
              ↓
     [Stage 1] File pre-validation (EL-2.0)
              ↓
     [Stage 2] Extract entities (EL-2.1/2.2/2.3)
              ↓
     [Stage 3] Schema mapping → canonical fields (EL-3.0)
              ↓
     [Stage 4] Unify → write to plenum_cafm DB
              ↓
              Done! Data is in the unified database
```

Your cleaned, mapped, validated data is now ready for:
- **Layer 5:** Specialist data agents (asset analysis, WO triage, etc.)
- **Layer 6:** Orchestration (automated decision-making)
- **Layer 7:** Query responses & document generation

---

## 🎓 Key Concepts Summary

| Concept | Meaning |
|---------|---------|
| **Tier 1** | Deterministic mapping (exact, alias, regex, LLM) |
| **Tier 2** | Semantic mapping (embedding similarity) |
| **Mapping** | Converting customer columns → canonical fields |
| **Hierarchy** | Detecting FK relationships between tables |
| **GATE** | Human review checkpoint (pause + validate) |
| **EL (Evaluation Layer)** | Automated quality gate at each node |
| **IntermediateSchema** | Standardized contract passed to svc-ingestion |
| **Confidence** | 0-1 score for how confident the AI is |

---

## ✅ Why This Matters

**Without this pipeline:**
- Each customer requires manual column mapping → **weeks of work**
- Data quality issues go undetected → **bad downstream data**
- No validation → **garbage in, garbage out**

**With this pipeline:**
- Customer uploads file → **10 minutes later it's mapped, validated, and ready**
- 9 validation gates catch errors → **high data quality**
- Fully automated with human oversight → **fast + accurate**
- Handles 10+ different CMMS formats → **universal solution**

---

## 🚀 Your First Test

1. Go to Streamlit UI
2. Upload a CSV (try `assets.csv`)
3. Watch the progress report as it goes through all 9 nodes
4. At each GATE, approve/correct the AI suggestions
5. See the outputs (JSON, CSV, SQL, PDF) generated
6. Confirm final handoff to svc-ingestion

You now understand the complete flow! 🎉
