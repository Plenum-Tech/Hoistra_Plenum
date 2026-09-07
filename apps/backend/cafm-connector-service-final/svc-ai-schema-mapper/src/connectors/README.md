# Platform Schema Connectors

Schema connectors for svc-ai-schema-mapper fetch live data from CMMS platforms and convert it to canonical mapper format.

## Fiix Connector

### Overview

The Fiix connector connects to a live Fiix CMMS instance, fetches schema from core objects, and automatically builds a field mapper for svc-ai-schema-mapper.

**Instead of manually defining field mappings, you get them directly from your Fiix instance.**

### Usage

#### 1. Backend Setup (app.py)

Fiix endpoints are automatically available at:

- `POST /api/platforms/fiix/test-connection` — Verify Fiix credentials
- `POST /api/platforms/fiix/fetch-schema` — Extract full schema

#### 2. Streamlit UI

1. Open **Config** panel (left sidebar)
2. Select "Fiix" from **Source CMMS** dropdown
3. Expand **Connect to Fiix CMMS** section
4. Enter credentials:
   - **Fiix Subdomain** (e.g., `plenumtechnology`)
   - **App Key**
   - **Access Key**
   - **Secret Key**
5. Click **Test Fiix Connection** → Should show ✅ success
6. Click **Fetch Fiix Schema** → Automatically loads mapper

#### 3. Upload CSV/Excel

Once Fiix schema is loaded, upload your file and the pipeline uses Fiix fields as the canonical reference.

### What Gets Extracted

The connector samples these Fiix core objects:

```
Asset, WorkOrder, ScheduledMaintenance, User, Priority,
MaintenanceType, WorkOrderStatus, AssetCategory, Stock,
Business, MeterReading, Project, TaskGroup
```

For each object, it extracts all field names and builds mappings like:

```json
{
  "source_system": "Fiix",
  "canonical_fields": {
    "asset_code": {
      "fiix_field": "strCode",
      "source_object": "Asset",
      "description": "Mapped from Fiix Asset.strCode"
    }
  },
  "vendor_aliases": {
    "strCode": "asset_code",
    "strName": "asset_name"
  }
}
```

### Configuration

Set these environment variables (or add to `.env`):

```env
FIIX_ENABLED=true
FIIX_SUBDOMAIN=plenumtechnology
FIIX_APP_KEY=macmmsackp...
FIIX_ACCESS_KEY=macmmsaakp...
FIIX_SECRET_KEY=macmmsaskp...
FIIX_TIMEOUT=30
```

### Error Handling

| Error | Cause | Solution |
|-------|-------|----------|
| "Connection failed" | Bad credentials | Verify subdomain, keys in Fiix |
| "Request timed out" | Large Fiix instance | Increase timeout or reduce sample size |
| "Cannot connect to API" | Backend unreachable | Check svc-ai-schema-mapper service is running |

### How It Works

```
User inputs Fiix credentials
        ↓
Backend connects via FiixAPI
        ↓
FiixSchemaConnector.get_mapper_config()
        ↓
Sample core Fiix objects (1 record each)
        ↓
Extract field names from samples
        ↓
Build mapper: fiix_field_name → canonical_field
        ↓
Return JSON mapper format
        ↓
Streamlit loads mapper into session
        ↓
User uploads CSV/Excel
        ↓
Nodes 1-3 use Fiix mapper instead of manual JSON
```

### Implementation Details

#### FiixAPI class
- Handles HMAC-SHA256 authentication
- Manages HTTP session with retries
- Provides `find_one()` to sample objects

#### FiixSchemaConnector class
- Orchestrates schema extraction
- Samples CORE_OBJECTS
- Builds field_mappings dictionary
- Returns mapper in svc-ai-schema-mapper format

#### Key Methods

```python
connector = FiixSchemaConnector(
    subdomain="plenumtechnology",
    app_key="...",
    access_key="...",
    secret_key="..."
)

# Test connectivity
if connector.api.test_connection():
    print("Connected!")

# Get mapper config
mapper = connector.get_mapper_config()
# Returns: {"source_system": "Fiix", "canonical_fields": {...}, ...}
```

### Future Enhancements

- [ ] Cache schema in Redis (TTL: 24h)
- [ ] Support Maximo, SAP PM, Archibus connectors
- [ ] Custom field filtering (only extract specific objects)
- [ ] Field metadata (data type, nullable, etc.)
- [ ] Automatic hierarchy detection from Fiix FKs

### Integration with svc-ai-schema-mapper Pipeline

Once Fiix schema is loaded:

1. **Node 1** (Ingest): Parse CSV file
2. **Node 2** (Deterministic Mapper): Use Fiix fields as canonical reference
3. **Node 3** (Semantic Mapper): Embed Fiix field descriptions for unresolved fields
4. **Node 4+**: Standard hierarchy/preprocessing/output

No changes to existing pipeline — Fiix mapper is just another input source.
