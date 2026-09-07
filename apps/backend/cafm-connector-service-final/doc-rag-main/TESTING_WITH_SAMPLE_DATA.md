# Testing Guide with Sample Assets Data

This guide walks through testing the complete pipeline with realistic sample data:
- **30 assets** (HVAC, elevators, pumps, electrical systems)
- **Maintenance report** referencing multiple assets
- **Sample queries** to validate matching

---

## Quick Start (One Command)

```bash
bash scripts/test_with_sample_data.sh
```

This automated script:
1. Seeds 30 assets from CSV
2. Uploads maintenance report
3. Matches document to assets
4. Runs 4 test queries
5. Generates test report

---

## Manual Step-by-Step

### Prerequisites

Start the API:
```bash
docker compose up -d
# OR
uvicorn app.main:app --port 8000
```

---

### Step 1: Seed Assets Database

The sample CSV contains 30 assets:
- 5 HVAC units (AHU-001, AHU-002, CHW-001, CHW-002, CT-001)
- 4 Elevators (EL-001 to EL-004)
- 3 Pumps (PUMP-001 to PUMP-003)
- 2 Emergency Generators (GEN-001, GEN-002)
- 2 UPS systems (UPS-001, UPS-002)
- Building automation, security, fire safety, lighting, etc.

**Seed the assets:**

```bash
python -m scripts.seed_row_index \
    --table assets \
    --csv sample_data/assets.csv
```

**Expected output:**
```
Seeded 30 rows into row_semantic_index
```

**What this does:**
- Reads `sample_data/assets.csv`
- Embeds each row (combining asset_code, asset_name, location, etc.)
- Stores in `row_semantic_index` table for matching

---

### Step 2: Upload Maintenance Report

The sample document is a quarterly maintenance report that mentions:
- AHU-001, AHU-002 (air handlers)
- CHW-001, CHW-002 (chillers)
- CT-001 (cooling tower)
- EL-001, EL-002, EL-003 (elevators)
- GEN-001 (generator)
- UPS-001 (UPS)
- PUMP-001, PUMP-002 (pumps)
- BMS-001 (building automation)

**Upload the document:**

```bash
curl -X POST http://localhost:8000/documents/upload \
    -F "file=@sample_data/maintenance_report_q4_2024.txt"
```

**Expected response:**
```json
{
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "indexed",
  "file_name": "maintenance_report_q4_2024.txt",
  "num_pages": 1,
  "num_chunks": 5,
  "document_type": "other",
  "processing_time_ms": 432
}
```

**Save the `document_id` - you'll need it for the next steps!**

---

### Step 3: Match Document to Assets

Replace `{document_id}` with your actual ID from Step 2:

```bash
curl -X POST http://localhost:8000/documents/{document_id}/match-rows \
    -H "Content-Type: application/json" \
    -d '{
        "confidence_threshold": 0.3,
        "group_by_table": true
    }'
```

**Expected response:**
```json
{
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "file_name": "maintenance_report_q4_2024.txt",
  "total_chunks_analyzed": 5,
  "unique_rows_matched": 14,
  "matched_rows": [
    {
      "source_table": "assets",
      "row_pk": "AHU-001",
      "confidence": 0.87,
      "match_method": "exact_key",
      "row_data": {
        "asset_code": "AHU-001",
        "asset_name": "Main Air Handler Unit North",
        "category": "HVAC",
        "building": "Building A",
        "floor": "5",
        "location": "Mechanical Room 501",
        "manufacturer": "Trane",
        "model": "CGAM-100",
        "status": "Operational",
        "last_service_date": "2024-11-20",
        "next_service_due": "2025-02-20"
      },
      "evidence": "asset_code: AHU-001. asset_name: Main Air Handler Unit North...",
      "chunk_ids": ["chunk-abc-2"],
      "chunk_count": 1
    },
    {
      "source_table": "assets",
      "row_pk": "EL-001",
      "confidence": 0.85,
      "match_method": "exact_key",
      "row_data": {
        "asset_code": "EL-001",
        "asset_name": "Main Elevator North Wing",
        "category": "Elevator",
        "building": "Building A",
        "manufacturer": "Otis",
        "model": "Gen2",
        "status": "Operational"
      },
      "evidence": "asset_code: EL-001. asset_name: Main Elevator North Wing...",
      "chunk_ids": ["chunk-abc-3"],
      "chunk_count": 1
    }
  ],
  "by_table": {
    "assets": 14
  },
  "latency_ms": 187
}
```

**What to validate:**
- ✓ `unique_rows_matched` should be ~12-16 (depending on confidence threshold)
- ✓ Key assets mentioned in report should be matched: AHU-001, AHU-002, EL-001, CHW-001, etc.
- ✓ Each row has `confidence` ≥ 0.3
- ✓ `row_data` contains full asset details (14 columns)

---

### Step 4: Test Queries

Now you can ask natural language questions and get both answers and matched assets:

#### Query 1: HVAC Equipment

```bash
curl -X POST http://localhost:8000/rag/query \
    -H "Content-Type: application/json" \
    -d '{
        "query": "What HVAC equipment was serviced in Q4?",
        "top_k": 5
    }'
```

**Expected matched assets:**
- AHU-001 (Main Air Handler Unit North)
- AHU-002 (Main Air Handler Unit South)
- CHW-001 (Primary Chiller Plant)
- CHW-002 (Secondary Chiller Plant)
- CT-001 (Cooling Tower Main)

#### Query 2: Elevator Status

```bash
curl -X POST http://localhost:8000/rag/query \
    -H "Content-Type: application/json" \
    -d '{
        "query": "Which elevators were inspected and what was their status?",
        "top_k": 5
    }'
```

**Expected matched assets:**
- EL-001 (Main Elevator North Wing)
- EL-002 (Main Elevator South Wing)
- EL-003 (Service Elevator)

#### Query 3: Generator

```bash
curl -X POST http://localhost:8000/rag/query \
    -H "Content-Type: application/json" \
    -d '{
        "query": "What is the status of the emergency generator?",
        "top_k": 3
    }'
```

**Expected matched assets:**
- GEN-001 (Emergency Generator Main)

#### Query 4: Issues Requiring Follow-up

```bash
curl -X POST http://localhost:8000/rag/query \
    -H "Content-Type: application/json" \
    -d '{
        "query": "What equipment needs parts or follow-up maintenance?",
        "top_k": 5
    }'
```

**Expected matched assets:**
- AHU-002 (belt replacement needed)
- UPS-001 (battery replacement scheduled)
- EL-001 (annual inspection due)

#### Query 5: Row-Only Search (No LLM Answer)

If you just want matched rows without generating an answer (faster/cheaper):

```bash
curl -X POST http://localhost:8000/rag/rows \
    -H "Content-Type: application/json" \
    -d '{
        "query": "Building A basement equipment",
        "top_k": 10
    }'
```

**Expected matched assets:**
- CHW-001, CHW-002 (chillers)
- GEN-001, GEN-002 (generators)
- UPS-001 (UPS)
- PUMP-001, PUMP-002, PUMP-003 (pumps)

---

### Step 5: Debug Analysis (Optional)

See which chunks matched which assets:

```bash
curl "http://localhost:8000/documents/{document_id}/match-rows/debug?show_all_chunks=true&confidence_threshold=0.3"
```

This shows:
- Every chunk in the document
- Which assets each chunk matched
- Confidence scores
- Chunks with no matches (false negatives)

**Use this to:**
- Identify missing matches (assets mentioned but not matched)
- Tune `confidence_threshold`
- Validate match quality

---

## Sample Data Details

### Assets CSV Structure

The `sample_data/assets.csv` contains these columns:
- `asset_code` - Primary key (AHU-001, EL-001, etc.)
- `asset_name` - Full name
- `category` - HVAC, Elevator, Pump, Electrical, etc.
- `building` - Building A or Building B
- `floor` - Floor number or location
- `location` - Specific room/area
- `manufacturer` - Equipment manufacturer
- `model` - Model number
- `serial_number` - Serial number
- `install_date` - Installation date
- `last_service_date` - Last service date
- `next_service_due` - Next scheduled service
- `status` - Operational, etc.
- Additional maintenance fields

### Maintenance Report Content

The report includes detailed sections for:
- HVAC Systems (5 assets)
- Elevator Systems (3 assets)
- Electrical Systems (2 assets)
- Pumps and Motors (2 assets)
- Building Automation (1 asset)
- Summary of issues and recommendations

---

## Expected Results Summary

With the sample data, you should see:

| Metric | Expected Value |
|--------|----------------|
| Assets seeded | 30 |
| Document chunks | 5-8 |
| Assets matched | 12-16 |
| Match rate | 80-100% of chunks |
| Top confidence | 0.85-0.95 (exact_key matches) |

**Assets that should match:**
- ✓ AHU-001, AHU-002 (air handlers)
- ✓ CHW-001, CHW-002 (chillers)
- ✓ CT-001 (cooling tower)
- ✓ EL-001, EL-002, EL-003 (elevators)
- ✓ GEN-001 (generator)
- ✓ UPS-001 (UPS)
- ✓ PUMP-001, PUMP-002 (pumps)
- ✓ BMS-001 (BMS)

---

## Troubleshooting

**Problem:** `unique_rows_matched: 0`
- Run: `curl http://localhost:8000/` and check if `row_index_size > 0`
- Re-run Step 1 to seed the database

**Problem:** Low match count (< 10)
- Check confidence threshold (try lowering to 0.25)
- Verify OPENAI_API_KEY is set (embeddings need to be real, not mock)

**Problem:** Wrong assets matching
- Raise confidence threshold to 0.5
- Check the debug endpoint to see why

---

## Next Steps

1. **Modify the sample data:**
   - Edit `sample_data/assets.csv` to match your real asset schema
   - Edit `sample_data/maintenance_report_q4_2024.txt` to reference your assets

2. **Test with your real data:**
   - Export your asset database to CSV
   - Upload real maintenance reports/contracts
   - Validate matching quality with the debug endpoint

3. **Integrate into your application:**
   - Use the same API calls shown here
   - Build a UI that displays `matched_rows` alongside answers
   - Create dashboards showing asset coverage across documents
