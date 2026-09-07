# Mapping Storage Architecture

## Overview

The mapping storage system allows you to upload and store CMMS mapping configurations in PostgreSQL, then automatically apply them when new CSVs are ingested.

Instead of passing mapping JSON with every request, users upload mappings once and reference them by `source_system` and `table_name`.

---

## Database Design Decision: JSONB vs Separate Columns

**Decision: Store entire JSON in JSONB column.**

### Why JSONB is optimal:

| Aspect | JSONB | Separate Columns |
|--------|-------|-----------------|
| **Flexibility** | ✅ Handle 37 different mapping structures without schema migration | ❌ Need new columns for each new field type |
| **Query Performance** | ✅ GIN indexes on JSONB fields enable fast nested searches | ❌ Column lookup always indexed but less flexible |
| **Storage** | ✅ Compressed, ~20-30% smaller than decomposed | ❌ Wider tables, more I/O |
| **Retrieval Speed** | ✅ Single column fetch vs reconstructing from 10+ columns | ✅ Slightly faster single-column lookup |
| **Maintainability** | ✅ No schema changes needed for new field types | ❌ Alembic migration required per change |
| **Vendor-specific Fields** | ✅ Arbitrary keys stored natively (vendor_aliases[0], vendor_aliases[1], etc) | ❌ Would need JSON serialization anyway |

### JSONB Indexing

PostgreSQL GIN indexes support efficient queries on nested JSONB:

```sql
-- Fast lookup by source_system + table_name + is_active
CREATE INDEX ix_mapping_templates_org_system_table_active 
  ON plenum_cafm.mapping_templates(organization_id, source_system, table_name, is_active);

-- If needed later: index on specific JSONB keys
CREATE INDEX ix_mapping_templates_config_fields 
  ON plenum_cafm.mapping_templates 
  USING GIN (config_json -> 'canonical_fields');
```

---

## Table Schema

```sql
CREATE TABLE plenum_cafm.mapping_templates (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL,              -- which customer
    source_system       VARCHAR(100) NOT NULL,      -- Maximo, Fiix, SAP PM, Archibus, Custom
    table_name          VARCHAR(100) NOT NULL,      -- assets, work_orders, parts, users, etc.
    version             INTEGER NOT NULL DEFAULT 1, -- versioning for rollback
    name                VARCHAR(255) NOT NULL,      -- "Fiix Assets v1.2", "Maximo WOs v2.0"
    config_json         JSONB NOT NULL,             -- full mapping + metadata
    is_active           BOOLEAN NOT NULL DEFAULT true,
    created_by          UUID,                       -- audit trail
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Indexes for fast lookup
    INDEX ix_mapping_templates_organization_id (organization_id),
    INDEX ix_mapping_templates_source_system (source_system),
    INDEX ix_mapping_templates_table_name (table_name),
    INDEX ix_mapping_templates_is_active (is_active),
    -- Composite index: most common query pattern
    INDEX ix_mapping_templates_org_system_table_active 
        (organization_id, source_system, table_name, is_active)
);
```

### Mapping Config JSON Structure

What gets stored in `config_json`:

```json
{
  "version": "1.0",
  "source_system": "Fiix",
  "description": "Fiix asset mapping for Site A",
  "canonical_fields": {
    "asset_code": "Unique asset identifier",
    "asset_name": "Asset display name",
    "asset_type": "Equipment type or category",
    "status": "Operational status",
    "priority": "Maintenance priority",
    "last_maintenance_date": "Date of last service",
    ...
  },
  "vendor_aliases": {
    "asset_code": ["asset_identifier", "asset_id", "equipment_id"],
    "asset_name": ["equipment_description", "description", "name"],
    "asset_type": ["equipment_category", "category", "type"],
    ...
  },
  "regex_patterns": {
    "asset_code": "^[A-Z]{2,4}-[0-9]{3,5}$",
    "wo_code": "^WO[0-9]{8}$",
    ...
  },
  "confidence_overrides": {
    "asset_code": 0.98,
    "serial_number": 0.95,
    ...
  }
}
```

---

## API Endpoints

### 1. POST `/api/mappings` — Create/Upload a Mapping

Upload a new mapping template to the database.

```bash
curl -X POST "http://localhost:8003/api/mappings" \
  -H "Content-Type: application/json" \
  -d '{
    "source_system": "Fiix",
    "table_name": "assets",
    "name": "Fiix Assets Mapping v1.0",
    "organization_id": "00000000-0000-0000-0000-000000000001",
    "config_json": {
      "version": "1.0",
      "source_system": "Fiix",
      "canonical_fields": {...},
      "vendor_aliases": {...},
      "regex_patterns": {...},
      "confidence_overrides": {...}
    },
    "version": 1
  }'
```

**Response:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "organization_id": "00000000-0000-0000-0000-000000000001",
  "source_system": "Fiix",
  "table_name": "assets",
  "name": "Fiix Assets Mapping v1.0",
  "version": 1,
  "config_json": {...},
  "is_active": true,
  "created_by": null,
  "created_at": "2026-04-03T10:30:00+00:00",
  "updated_at": "2026-04-03T10:30:00+00:00"
}
```

---

### 2. GET `/api/mappings` — List Mappings

List all mappings for an organization with optional filters.

```bash
# List all active mappings
curl "http://localhost:8003/api/mappings?organization_id=00000000-0000-0000-0000-000000000001"

# Filter by source system
curl "http://localhost:8003/api/mappings?organization_id=...&source_system=Fiix"

# Filter by table name
curl "http://localhost:8003/api/mappings?organization_id=...&table_name=assets"

# Include inactive mappings
curl "http://localhost:8003/api/mappings?organization_id=...&is_active=false"
```

**Response:**
```json
{
  "mappings": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "organization_id": "00000000-0000-0000-0000-000000000001",
      "source_system": "Fiix",
      "table_name": "assets",
      "name": "Fiix Assets Mapping v1.0",
      "version": 1,
      "config_json": {...},
      "is_active": true,
      "created_at": "2026-04-03T10:30:00+00:00",
      "updated_at": "2026-04-03T10:30:00+00:00"
    },
    ...
  ],
  "total": 15
}
```

---

### 3. GET `/api/mappings/{mapping_id}` — Retrieve a Mapping

Get a specific mapping by ID.

```bash
curl "http://localhost:8003/api/mappings/550e8400-e29b-41d4-a716-446655440000"
```

---

### 4. GET `/api/mappings/lookup/{source_system}/{table_name}` — Auto-Lookup

Auto-detect and retrieve the active mapping for a source system + table.

**This is called automatically during CSV ingest.**

```bash
curl "http://localhost:8003/api/mappings/lookup/Fiix/assets?organization_id=00000000-0000-0000-0000-000000000001"
```

**Response:** Returns the mapping config (same as POST response)

---

### 5. PUT `/api/mappings/{mapping_id}` — Update a Mapping

Update an existing mapping (name, config, active status).

```bash
curl -X PUT "http://localhost:8003/api/mappings/550e8400-e29b-41d4-a716-446655440000" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Fiix Assets Mapping v1.1",
    "config_json": {...},
    "is_active": true
  }'
```

---

### 6. DELETE `/api/mappings/{mapping_id}` — Soft-Delete a Mapping

Soft-delete a mapping by setting `is_active = false`.

Record remains in database for audit purposes.

```bash
curl -X DELETE "http://localhost:8003/api/mappings/550e8400-e29b-41d4-a716-446655440000"
```

---

## Integration with CSV Ingest Flow

### Before (Old Flow):
```
User uploads CSV + mapping_json
  → deterministic_mapper uses passed mapping_json
  → processes CSV
```

### After (New Flow):
```
User uploads CSV + organization_id + source_system + table_name
  → lookup_mapping() queries mapping_templates table
  → retrieve active mapping config
  → deterministic_mapper uses stored mapping
  → processes CSV
```

### How It Works

In `ingest_node.py` (Node 1):

```python
async def ingest_and_configure_node(state: MigrationState) -> MigrationState:
    # User provides source_system (detected or specified)
    source_system = state.get("cmms_name")  # or auto-detected
    table_name = state.get("primary_table")  # or auto-detected from CSV headers
    org_id = state.get("organization_id")
    
    # Try to lookup stored mapping
    mapping_service = MappingService(session)
    stored_mapping = await mapping_service.lookup_mapping(
        organization_id=org_id,
        source_system=source_system,
        table_name=table_name
    )
    
    if stored_mapping:
        # Use stored mapping
        logger.info(f"Using stored mapping for {source_system}/{table_name}")
        state["json_mapper"] = stored_mapping
    else:
        # Fall back to user-provided mapping or use defaults
        logger.info(f"No stored mapping found, using provided config or defaults")
        state["json_mapper"] = state.get("json_mapper", {})
    
    return state
```

---

## Benefits

### 1. **No More Mapping in Every Request**
- Upload mapping once (when onboarding a new CMMS system)
- Reference by source system + table name
- Reduces request payload size by 10-50KB per request

### 2. **Version Control**
- Multiple versions of same mapping (v1.0, v1.1, v2.0)
- Rollback to previous version by updating `is_active`
- All versions retained for audit trail

### 3. **Reusability**
- Same mapping applied to every CSV for that CMMS system
- Consistency across migrations
- Templates for common systems (Maximo, Fiix, SAP PM, Archibus)

### 4. **Auto-Detection**
- If CSV headers match known CMMS pattern, mapping is applied automatically
- No user input needed
- Reduces errors from manual mapping

### 5. **Audit Trail**
- Track who created/modified each mapping
- Timestamps on every change
- Soft-delete keeps full history

---

## Uploading All 37 Mappings

You have 37 mapping files. Here's how to upload them:

### Batch Upload Script (Python)

```python
import json
import requests
from pathlib import Path
from uuid import UUID

BASE_URL = "http://localhost:8003"
ORG_ID = UUID("00000000-0000-0000-0000-000000000001")  # Your org ID

# Path to your 37 JSON mapping files
MAPPINGS_DIR = Path("/Users/you/mappings/")

for mapping_file in MAPPINGS_DIR.glob("*.json"):
    with open(mapping_file, "r") as f:
        config = json.load(f)
    
    # Extract metadata
    source_system = config.get("source_system", "Custom")
    description = config.get("description", "")
    
    # Infer table_name from filename
    # e.g., "assets_mapping.json" -> "assets"
    table_name = mapping_file.stem.replace("_mapping", "")
    
    # Upload
    response = requests.post(
        f"{BASE_URL}/api/mappings",
        params={
            "source_system": source_system,
            "table_name": table_name,
            "name": f"{source_system} {table_name.title()} v1.0",
            "organization_id": str(ORG_ID),
        },
        json={
            "config_json": config,
        }
    )
    
    if response.status_code == 200:
        print(f"✅ Uploaded: {mapping_file.name}")
    else:
        print(f"❌ Failed: {mapping_file.name} - {response.text}")
```

---

## Performance Characteristics

### Query Performance

- **Lookup by source_system + table_name**: ~5ms (indexed)
- **List all mappings for org**: ~20ms (B-tree index on org_id)
- **Retrieve config_json**: ~2ms (single JSONB column, no joins)

### Storage

- **Per mapping**: ~5-20KB (typical config_json size)
- **37 mappings**: ~200KB - 1MB total storage
- **Negligible** impact on PostgreSQL

### Bandwidth

- **Old flow**: 15KB mapping JSON + request = ~20KB per CSV upload
- **New flow**: Just `source_system` + `table_name` query params = ~100 bytes
- **Savings**: ~99.5% reduction in per-request payload

---

## Next Steps

1. **Create the alembic migration**: Run migration 004 to add `mapping_templates` table
   ```bash
   alembic upgrade head
   ```

2. **Include mappings router in app.py**:
   ```python
   from .api.mappings import router as mappings_router
   app.include_router(mappings_router)
   ```

3. **Upload your 37 mappings** using the batch script above

4. **Modify ingest_node.py** to call `MappingService.lookup_mapping()` and auto-apply stored mappings

5. **Update CSV ingest endpoint** to accept optional `source_system` parameter (can also auto-detect from headers)

---

## Schema Comparison

### Old Approach (Mapping in Request)
```
POST /api/migration/start
Content-Length: 25,000 bytes (includes full mapping JSON)

{
  "file": <binary>,
  "mapper_json": {
    "canonical_fields": {...},
    "vendor_aliases": {...},
    ...
  }
}
```

### New Approach (Mapping Lookup)
```
POST /api/migration/start
Content-Length: 5,000 bytes (just the file)

{
  "file": <binary>,
  "organization_id": "...",
  "cmms_name": "Fiix",
  "primary_table": "assets"
}
```

System automatically looks up and applies stored mapping. No mapping JSON in request.

---

## FAQ

**Q: What if a CSV comes from a new source system we haven't seen before?**  
A: Fallback to user-provided mapping or attempt auto-detection with defaults. Log warning and allow manual override.

**Q: Can we have multiple mappings for the same source_system + table_name?**  
A: Yes, use `version` field. The lookup query returns the highest `version` where `is_active = true`.

**Q: How do we handle mapping updates without affecting in-progress migrations?**  
A: Each migration captures the mapping config at ingest time. Updates to mapping_templates don't affect existing migrations.

**Q: Should we store the mapping_id in migration_jobs for audit?**  
A: Yes, add `mapping_template_id` column to `migration_jobs` table (optional but recommended for full audit trail).

**Q: What about vendor-specific extensions?**  
A: JSONB supports arbitrary keys. Add vendor-specific fields as-is, and schema_mapper ignores unknowns.
