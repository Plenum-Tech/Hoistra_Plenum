# JSON Mapper Architecture — Customer-Specific Field Mappings

**Status:** Design Document  
**Date:** 2026-04-02  
**Phase:** Testing (Streamlit upload) → Production (Database table)

---

## Problem Statement

Currently, field mappings are **hardcoded** in the codebase:
- `src/matchers/cmms_aliases.py` — Hardcoded vendor aliases (Maximo, Fiix, SAP PM, etc.)
- `canonical_fields` — Hardcoded list of target fields
- `regex_patterns.py` — Hardcoded regex patterns

**Issue:** Different customers have different:
- Field naming conventions
- Mapping rules
- Custom fields
- Business logic for field transformations

**Solution:** Read mappings from **customer-provided JSON files** instead of hardcoded values.

---

## Design Overview

```
┌─────────────────────────────────────────────────────────────┐
│ CUSTOMER PROVIDES MAPPING JSON                              │
│ (Downloaded from their CMMS, custom fields, rules)          │
└──────────────────────────┬──────────────────────────────────┘
                           │
                ┌──────────▼─────────────┐
                │ TESTING PHASE (NOW)   │
                │ Streamlit File Upload │
                │ ↓                     │
                │ Temp storage/memory   │
                └──────────┬────────────┘
                           │
                ┌──────────▼──────────────────┐
                │ PRODUCTION (LATER)         │
                │ Database table: json_mapper│
                │ ↓                          │
                │ Keyed by customer_id       │
                └──────────┬─────────────────┘
                           │
                ┌──────────▼────────────────────────┐
                │ DETERMINISTIC MAPPER (Node 2)    │
                │ Reads from JSON instead of code  │
                │                                  │
                │ 1. Exact match (from JSON)       │
                │ 2. Aliases (from JSON)           │
                │ 3. Patterns (from JSON)          │
                │ 4. Haiku constrained             │
                └──────────────────────────────────┘
```

---

## JSON Mapper File Format

### Example: `maximo_mapping.json`
```json
{
  "version": "1.0",
  "source_system": "Maximo",
  "customer_id": "customer-abc-123",
  "description": "Maximo asset inventory mapping for ABC Manufacturing",
  "canonical_fields": {
    "asset_id": "Unique identifier for asset (PK)",
    "asset_code": "Human-readable asset code",
    "asset_name": "Display name of asset",
    "asset_type": "Category (pump, motor, fan, etc.)",
    "location_id": "FK to location table",
    "location_description": "Site location (building/room)",
    "department_id": "FK to department",
    "department_name": "Department name",
    "serial_number": "Manufacturer serial",
    "manufacturer_name": "Equipment manufacturer",
    "model_number": "Equipment model",
    "acquisition_date": "Purchase date",
    "condition_status": "Operational status (good/fair/poor)",
    "last_maintenance_date": "Last service date"
  },
  "vendor_aliases": {
    "asset_id": [
      "ASSET_ID",
      "AssetID",
      "asset_number",
      "asset_no",
      "id"
    ],
    "asset_code": [
      "ASSET_CODE",
      "AssetCode",
      "code",
      "asset_code_id",
      "asset_tag"
    ],
    "location_description": [
      "LOCATION",
      "Location",
      "loc",
      "site",
      "building_room",
      "facility"
    ],
    "department_name": [
      "DEPARTMENT",
      "Department",
      "dept",
      "division"
    ],
    "serial_number": [
      "SERIAL",
      "Serial",
      "serial_num",
      "sn"
    ],
    "manufacturer_name": [
      "MANUFACTURER",
      "Manufacturer",
      "mfg",
      "brand"
    ],
    "model_number": [
      "MODEL",
      "Model",
      "model_num",
      "model_name"
    ],
    "acquisition_date": [
      "ACQUISITION_DATE",
      "PURCHASE_DATE",
      "acquired_date",
      "purchase_date",
      "date_acquired"
    ],
    "condition_status": [
      "CONDITION",
      "Condition",
      "status",
      "asset_status",
      "operational_status"
    ],
    "last_maintenance_date": [
      "LAST_MAINTENANCE",
      "last_service_date",
      "last_maint_date",
      "maintenance_date"
    ]
  },
  "regex_patterns": {
    "asset_id": {
      "patterns": ["^asset_?(?:id|number|num|no)$", "^id$"],
      "confidence": 0.90
    },
    "asset_code": {
      "patterns": ["^asset_?code$", "^code$", "^asset_tag$"],
      "confidence": 0.88
    },
    "location_description": {
      "patterns": ["^location", "^loc_", "^site", "^building"],
      "confidence": 0.85
    },
    "serial_number": {
      "patterns": ["^serial", "^sn$", "^serial_num"],
      "confidence": 0.90
    },
    "manufacturer_name": {
      "patterns": ["^mfg", "^manufacturer", "^brand"],
      "confidence": 0.85
    }
  },
  "custom_transformations": {
    "location_description": {
      "type": "concat",
      "fields": ["building", "room"],
      "separator": " - ",
      "description": "Combine building and room into location description"
    },
    "condition_status": {
      "type": "map",
      "values": {
        "1": "good",
        "2": "fair",
        "3": "poor",
        "G": "good",
        "F": "fair",
        "P": "poor"
      },
      "description": "Map numeric/letter codes to condition text"
    }
  },
  "excluded_fields": [
    "internal_id",
    "system_notes",
    "audit_timestamp"
  ],
  "confidence_overrides": {
    "asset_id": 0.99,
    "asset_code": 0.95
  }
}
```

---

## Database Table: `json_mapper`

### Schema (For Production)

```sql
CREATE TABLE IF NOT EXISTS plenum_cafm.json_mapper (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  customer_id UUID NOT NULL REFERENCES customers(id),
  source_system VARCHAR(100) NOT NULL,  -- "Maximo", "Fiix", "SAP PM", etc.
  version VARCHAR(10) DEFAULT "1.0",
  
  -- The JSON mapping configuration
  canonical_fields JSONB NOT NULL,      -- Target field definitions
  vendor_aliases JSONB NOT NULL,        -- Source → target mappings
  regex_patterns JSONB NOT NULL,        -- Pattern-based matching rules
  custom_transformations JSONB,         -- Field transformations
  excluded_fields JSONB,                -- Fields to ignore
  confidence_overrides JSONB,           -- Confidence score tweaks
  
  -- Metadata
  description TEXT,
  uploaded_by UUID,
  uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  
  UNIQUE(customer_id, source_system),
  INDEX idx_customer_id (customer_id),
  INDEX idx_source_system (source_system)
);
```

---

## Phase 1: Testing (Current)

### Architecture: Streamlit + Memory/Temp Storage

```
Streamlit UI
  │
  ├─→ File Upload
  │   (customer provides JSON)
  │
  ├─→ Store in Session State
  │   (st.session_state["json_mapper"])
  │
  └─→ Pass to API
      POST /api/testing/ingest
      {
        "file": <csv>,
        "json_mapper": <json mapping>,  ← Customer mapping
        "cmms_name": "Maximo"
      }
      │
      ├─→ FastAPI Endpoint
      │   /api/testing/ingest-with-mapper
      │
      └─→ Node 1 (Ingest)
          ├─→ Load JSON mapper
          ├─→ Parse CSV
          └─→ Return state
              │
              └─→ Node 2 (Deterministic Mapper)
                  ├─→ Read aliases from JSON
                  ├─→ Read patterns from JSON
                  ├─→ Apply transformations
                  └─→ Return mapped fields
```

---

## Phase 2: Production (Later)

### Architecture: Database + API

```
Customer Portal
  │
  ├─→ Upload Mapping JSON
  │   (or auto-generate from sample data)
  │
  ├─→ Store in DB
  │   INSERT INTO plenum_cafm.json_mapper (...)
  │
  └─→ Migration Starts
      POST /api/migration/start
      {
        "source_blob_url": "...",
        "customer_id": "abc-123",  ← ← ← NEW
        "cmms_name": "Maximo"
      }
      │
      ├─→ Load JSON Mapper from DB
      │   SELECT * FROM json_mapper
      │   WHERE customer_id = ? AND source_system = ?
      │
      └─→ 9-Node Pipeline
          All nodes use loaded JSON mappings
```

---

## Implementation Steps

### Step 1: Create JSON Mapper Schema Classes

**File:** `src/schemas.py` (add)

```python
class CanonicalField(BaseModel):
    """Definition of a canonical target field."""
    name: str
    description: str
    required: bool = False
    data_type: Optional[str] = None  # "string", "date", "int", etc.

class VendorAliases(BaseModel):
    """Source field names that map to canonical fields."""
    mappings: Dict[str, List[str]]  # canonical → [source1, source2, ...]

class RegexPattern(BaseModel):
    """Regex-based field matching rules."""
    patterns: List[str]
    confidence: float
    description: Optional[str] = None

class CustomTransformation(BaseModel):
    """Field transformation logic."""
    type: Literal["concat", "map", "formula", "split"]
    fields: Optional[List[str]] = None
    values: Optional[Dict[str, str]] = None
    separator: Optional[str] = None
    description: Optional[str] = None

class JsonMapperConfig(BaseModel):
    """Complete JSON mapper configuration."""
    version: str = "1.0"
    source_system: str  # "Maximo", "Fiix", etc.
    customer_id: Optional[str] = None
    description: Optional[str] = None
    canonical_fields: Dict[str, str]  # field → description
    vendor_aliases: Dict[str, List[str]]  # canonical → [sources]
    regex_patterns: Optional[Dict[str, RegexPattern]] = None
    custom_transformations: Optional[Dict[str, CustomTransformation]] = None
    excluded_fields: Optional[List[str]] = None
    confidence_overrides: Optional[Dict[str, float]] = None
```

### Step 2: Update Streamlit to Upload JSON Mapper

**File:** `streamlit_app.py` (update)

```python
# Sidebar: JSON Mapper Upload Section
with st.sidebar.expander("📋 JSON Mapper Config"):
    st.markdown("Upload a custom JSON mapping for this customer")
    
    mapper_file = st.file_uploader(
        "Upload JSON mapper (optional)",
        type=["json"],
        help="Customer-specific field mappings"
    )
    
    if mapper_file:
        try:
            import json
            mapper_config = json.load(mapper_file)
            st.session_state.json_mapper = mapper_config
            st.success(f"✓ Loaded mapping for {mapper_config.get('source_system')}")
            
            # Show summary
            st.json({
                "source_system": mapper_config.get("source_system"),
                "canonical_fields": len(mapper_config.get("canonical_fields", {})),
                "vendor_aliases": len(mapper_config.get("vendor_aliases", {})),
                "regex_patterns": len(mapper_config.get("regex_patterns", {})),
            })
        except Exception as e:
            st.error(f"Failed to load JSON: {e}")
    
    # Also allow sample mapping template
    if st.button("📥 Download Sample Mapper"):
        sample = {
            "version": "1.0",
            "source_system": "Maximo",
            "canonical_fields": {
                "asset_id": "Unique identifier",
                "asset_code": "Human-readable code",
                # ... etc
            },
            "vendor_aliases": {
                "asset_id": ["ASSET_ID", "AssetID", "id"],
                # ... etc
            }
        }
        st.download_button(
            "Download sample_mapper.json",
            json.dumps(sample, indent=2),
            "sample_mapper.json",
            "application/json"
        )
```

### Step 3: New Testing Endpoint (with Mapper)

**File:** `src/app.py` (add new endpoint)

```python
@app.post(
    "/api/testing/ingest-with-mapper",
    response_model=TestIngestResponse,
    tags=["Testing"],
    summary="[Internal] Test Node 1 with custom JSON mapper",
)
async def test_ingest_with_mapper(
    file: UploadFile = File(...),
    mapper_json: str = Form(..., description="JSON mapper config as string"),
    cmms_name: str = Form("Custom"),
    organization_id: str = Form("00000000-0000-0000-0000-000000000001"),
) -> TestIngestResponse:
    """
    Test Node 1 (Ingest) with customer-provided JSON mapper.
    
    The JSON mapper defines:
    - canonical_fields: target field definitions
    - vendor_aliases: source → canonical mappings
    - regex_patterns: pattern-based matching
    - custom_transformations: field transformations
    """
    from .schemas import JsonMapperConfig
    
    migration_id = str(uuid4())
    t_start = time.monotonic()
    
    # Parse mapper JSON
    try:
        mapper_config = JsonMapperConfig(**json.loads(mapper_json))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid mapper JSON: {e}")
    
    # Read file bytes
    file_bytes = await file.read()
    
    # Build state with mapper
    state: dict = {
        "migration_id": migration_id,
        "organization_id": organization_id,
        "cmms_name": cmms_name,
        "source_file_bytes": file_bytes,
        "json_mapper": mapper_config.dict(),  # ← Custom mappings
        "event_log": [],
    }
    
    try:
        # Run Node 1 with mapper
        result_state = await ingest_node(state)
    except Exception as e:
        logger.exception(f"ingest_node failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
    duration_ms = (time.monotonic() - t_start) * 1000
    
    return TestIngestResponse(
        migration_id=migration_id,
        filename=file.filename,
        file_size_bytes=len(file_bytes),
        detected_file_format=result_state.get("detected_file_format"),
        detected_encoding=result_state.get("source_encoding"),
        detected_delimiter=result_state.get("source_delimiter"),
        row_count=result_state.get("row_count", 0),
        column_count=result_state.get("column_count", 0),
        el_m1_passed=result_state.get("el_m1_passed", False),
        duration_ms=round(duration_ms, 1),
    )
```

### Step 4: Update Deterministic Mapper to Read from JSON

**File:** `src/graph/nodes/deterministic_mapper.py` (major refactor)

**Before:**
```python
from ...matchers.cmms_aliases import get_cmms_alias
from ...matchers.regex_patterns import match_field_by_pattern

# Hardcoded canonical fields
CANONICAL_FIELDS = ["asset_id", "asset_code", ...]

async def deterministic_mapper_node(state: MigrationState) -> MigrationState:
    source_fields = state["column_names"]
    
    # Strategy 2: Vendor aliases (from hardcoded module)
    for field in source_fields:
        match = get_cmms_alias(field)  ← Hardcoded
        
    # Strategy 3: Regex (from hardcoded module)
    for field in unresolved:
        match = match_field_by_pattern(field)  ← Hardcoded
```

**After:**
```python
async def deterministic_mapper_node(state: MigrationState) -> MigrationState:
    source_fields = state["column_names"]
    json_mapper = state.get("json_mapper")  # ← From customer
    
    if json_mapper:
        # Use customer's mappings
        canonical_fields = json_mapper["canonical_fields"]
        vendor_aliases = json_mapper["vendor_aliases"]
        regex_patterns = json_mapper["regex_patterns"]
    else:
        # Fallback to defaults (production from DB)
        canonical_fields = await load_canonical_fields()
        vendor_aliases = await load_vendor_aliases()
        regex_patterns = await load_regex_patterns()
    
    # Strategy 1: Exact match (using JSON)
    for field in source_fields:
        if field in canonical_fields:
            # Match found
            confidence = 0.99
    
    # Strategy 2: Vendor aliases (using JSON)
    for canonical, sources in vendor_aliases.items():
        if field in sources:
            # Match found
            confidence = 0.95-0.98
    
    # Strategy 3: Regex patterns (using JSON)
    for canonical, pattern_config in regex_patterns.items():
        patterns = pattern_config["patterns"]
        for pattern in patterns:
            if re.match(pattern, field, re.IGNORECASE):
                confidence = pattern_config["confidence"]
```

---

## Testing Workflow (Phase 1)

### Scenario: Customer provides Maximo mapping

```bash
# 1. Get sample mapper template
curl http://localhost:8501  # Streamlit UI
# Download: sample_mapper.json

# 2. Customer customizes mapper
# Edit sample_mapper.json with their fields

# 3. Upload to Streamlit
# - Select CSV file
# - Upload JSON mapper
# - Click "Analyze with Custom Mapper"

# 4. Streamlit calls API
POST /api/testing/ingest-with-mapper
  file: assets.csv
  mapper_json: {"version": "1.0", "canonical_fields": {...}}
  cmms_name: Maximo

# 5. API runs Node 1 with mapper
# Returns: row_count, column_count, el_m1_passed, etc.

# 6. View results
# - CSV correctly parsed
# - Fields detected using custom aliases
# - Data quality metrics shown
```

---

## Migration Path: Testing → Production

### Testing Phase (Now)
```
Streamlit
  ↓
Upload JSON + CSV
  ↓
POST /api/testing/ingest-with-mapper
  ↓
In-memory JSON mapper
  ↓
Node 1 & 2 run with mapper
```

### Production Phase (Later)
```
Customer Portal
  ↓
Upload JSON → DB
  ↓
INSERT INTO json_mapper
  ↓
POST /api/migration/start
  {customer_id, source_system}
  ↓
Load JSON from DB
  WHERE customer_id = ? AND source_system = ?
  ↓
9-node pipeline uses mapper
```

---

## Benefits

✅ **Customer-Specific:** Each customer provides their own mappings  
✅ **Flexible:** Easy to add/change fields without code changes  
✅ **Testable:** Streamlit UI for QA validation  
✅ **Scalable:** DB-backed for production (later)  
✅ **Auditable:** JSON files stored as historical record  
✅ **AI-Enhanced:** Still supports Haiku constrained calls  

---

## JSON Mapper Structure (Detailed)

### canonical_fields
```json
{
  "asset_id": "Unique identifier for asset (PK)",
  "asset_code": "Human-readable asset code",
  "asset_name": "Display name of asset",
  "location_description": "Site location (building/room)"
}
```

### vendor_aliases
```json
{
  "asset_id": ["ASSET_ID", "AssetID", "asset_number", "id"],
  "asset_code": ["ASSET_CODE", "code", "asset_tag"],
  "location_description": ["LOCATION", "loc", "site"]
}
```

### regex_patterns
```json
{
  "asset_id": {
    "patterns": ["^asset_?(?:id|number|num|no)$", "^id$"],
    "confidence": 0.90,
    "description": "Match fields starting with asset"
  }
}
```

### custom_transformations
```json
{
  "location_description": {
    "type": "concat",
    "fields": ["building", "room"],
    "separator": " - ",
    "description": "Combine building and room"
  },
  "condition_status": {
    "type": "map",
    "values": {"1": "good", "2": "fair", "3": "poor"},
    "description": "Map status codes"
  }
}
```

---

## Files to Create/Modify

### New Files
- `fixtures/mappers/maximo_mapping.json` — Sample Maximo mapper
- `fixtures/mappers/fiix_mapping.json` — Sample Fiix mapper
- `fixtures/mappers/sap_pm_mapping.json` — Sample SAP PM mapper

### Files to Modify
- `src/schemas.py` — Add JsonMapperConfig + related classes
- `src/app.py` — Add `/api/testing/ingest-with-mapper` endpoint
- `streamlit_app.py` — Add JSON mapper upload section
- `src/graph/nodes/deterministic_mapper.py` — Read from JSON mapper
- `src/graph/nodes/ingest_node.py` — Accept json_mapper in state
- `src/graph/migration_graph.py` — Pass mapper through pipeline

---

## Next Steps

1. **Create JSON mapper schema** (Pydantic models)
2. **Update Streamlit** to upload JSON files
3. **Create testing endpoint** `/api/testing/ingest-with-mapper`
4. **Refactor deterministic_mapper** to read from JSON
5. **Create sample mappers** for common CMMS systems
6. **Test E2E** with custom mapping

---

**Status:** Ready for implementation  
**Estimated Effort:** 2-3 hours  
**Impact:** Enables customer-specific field mapping without code changes
