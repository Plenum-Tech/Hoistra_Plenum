# API Testing Guide - Document to Rows Matching

Complete step-by-step guide to test the document-to-rows matching pipeline.

## Prerequisites

1. Start the API server:
   ```bash
   docker compose up -d
   # OR locally:
   uvicorn app.main:app --port 8000
   ```

2. Seed test data (run once):
   ```bash
   python -m scripts.test_document_matching
   ```
   This creates 3 test rows: equipment.AHU-017, equipment.AHU-018, elevators.EL-001

---

## Step 1: Upload a Document

Create a test file:
```bash
cat > test_maintenance.txt << 'EOF'
MAINTENANCE REPORT - JANUARY 2025

Equipment inspected:
- AHU-017 in Building A, Floor 5 (Trane unit, filters replaced)
- AHU-018 in Building B, Floor 3 (Carrier unit, belt needed)
- Elevator EL-001 in Building A (safety systems OK, 45 kW load test passed)

All critical systems operational.
EOF
```

Upload it:
```bash
curl -X POST http://localhost:8000/documents/upload \
  -F "file=@test_maintenance.txt"
```

**Expected response:**
```json
{
  "document_id": "abc-123-def-456",
  "status": "indexed",
  "file_name": "test_maintenance.txt",
  "num_pages": 1,
  "num_chunks": 1,
  "document_type": "other",
  "processing_time_ms": 245
}
```

**Copy the `document_id` from the response!**

---

## Step 2: Match Document to Database Rows

Replace `{document_id}` with your actual document ID from Step 1:

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
  "document_id": "abc-123-def-456",
  "file_name": "test_maintenance.txt",
  "total_chunks_analyzed": 1,
  "unique_rows_matched": 3,
  "matched_rows": [
    {
      "source_table": "equipment",
      "row_pk": "AHU-017",
      "confidence": 0.49,
      "match_method": "exact_key",
      "row_data": {
        "equipment_id": "AHU-017",
        "equipment_name": "Main AHU - North Wing",
        "building": "Building A",
        "floor": 5,
        "manufacturer": "Trane",
        "model": "CGAM-100"
      },
      "evidence": "equipment_id: AHU-017. equipment_name: Main AHU North Wing...",
      "chunk_ids": ["chunk-abc-1"],
      "chunk_count": 1
    },
    {
      "source_table": "equipment",
      "row_pk": "AHU-018",
      "confidence": 0.49,
      "match_method": "exact_key",
      "row_data": {
        "equipment_id": "AHU-018",
        "equipment_name": "Service AHU - South Wing",
        "building": "Building B",
        "floor": 3,
        "manufacturer": "Carrier",
        "model": "39M"
      },
      "evidence": "equipment_id: AHU-018. equipment_name: Service AHU...",
      "chunk_ids": ["chunk-abc-1"],
      "chunk_count": 1
    },
    {
      "source_table": "elevators",
      "row_pk": "EL-001",
      "confidence": 0.49,
      "match_method": "exact_key",
      "row_data": {
        "elevator_id": "EL-001",
        "elevator_name": "Main Elevator - North Wing",
        "building": "Building A",
        "total_load_kw": 45,
        "manufacturer": "Otis"
      },
      "evidence": "elevator_id: EL-001. elevator_name: Main Elevator North...",
      "chunk_ids": ["chunk-abc-1"],
      "chunk_count": 1
    }
  ],
  "by_table": {
    "equipment": 2,
    "elevators": 1
  },
  "matched_rows_by_table": {
    "equipment": [...],
    "elevators": [...]
  },
  "latency_ms": 31
}
```

**What to validate:**
- ✓ `unique_rows_matched` should be 3
- ✓ Each row should have `confidence` ≥ 0.3
- ✓ `match_method` should be "exact_key" for these test cases
- ✓ `row_data` contains all database columns from your asset table

---

## Step 3: Debug Chunk-by-Chunk Matching (Optional)

See exactly which chunks matched which rows:

```bash
curl "http://localhost:8000/documents/{document_id}/match-rows/debug?show_all_chunks=true&confidence_threshold=0.25"
```

**Expected response:**
```json
{
  "document_id": "abc-123-def-456",
  "file_name": "test_maintenance.txt",
  "row_index_size": 3,
  "total_chunks": 1,
  "chunks_with_matches": 1,
  "chunks_without_matches": 0,
  "chunk_details": [
    {
      "chunk_id": "chunk-abc-1",
      "chunk_index": 0,
      "page": 1,
      "block_type": "paragraph",
      "section_label": "maintenance",
      "text": "MAINTENANCE REPORT - JANUARY 2025\n\nEquipment inspected:\n- AHU-017...",
      "text_length": 287,
      "matched_rows": [
        {
          "source_table": "equipment",
          "row_pk": "AHU-017",
          "confidence": 0.49,
          "match_method": "exact_key",
          "row_data": {...},
          "evidence": "equipment_id: AHU-017. equipment_name: Main AHU..."
        },
        {
          "source_table": "equipment",
          "row_pk": "AHU-018",
          "confidence": 0.49,
          "match_method": "exact_key",
          "row_data": {...},
          "evidence": "equipment_id: AHU-018..."
        },
        {
          "source_table": "elevators",
          "row_pk": "EL-001",
          "confidence": 0.49,
          "match_method": "exact_key",
          "row_data": {...},
          "evidence": "elevator_id: EL-001..."
        }
      ],
      "match_count": 3
    }
  ],
  "note": "Set show_all_chunks=true to include chunks with no matches. Adjust confidence_threshold to filter weak matches."
}
```

**What this shows:**
- Every chunk in the document
- Which rows each chunk matched
- Confidence score and match method for each
- Text content of each chunk
- Chunks with no matches (set `show_all_chunks=true`)

---

## Step 4: Natural Language Query with Row Grounding

Ask questions about the document and get matched database rows:

```bash
curl -X POST http://localhost:8000/rag/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What equipment was inspected?",
    "top_k": 5
  }'
```

**Expected response:**
```json
{
  "query_id": "query-xyz-789",
  "query_type": "factual",
  "answer": "The maintenance report inspected three pieces of equipment: AHU-017 (Main AHU in Building A, Floor 5), AHU-018 (Service AHU in Building B, Floor 3), and Elevator EL-001 in Building A.",
  "confidence": 0.85,
  "citations": [
    {
      "document_id": "abc-123-def-456",
      "file_name": "test_maintenance.txt",
      "page_start": 1,
      "page_end": 1,
      "section": "maintenance",
      "chunk_id": "chunk-abc-1",
      "quote": "AHU-017 in Building A, Floor 5 (Trane unit, filters replaced)"
    }
  ],
  "matched_rows": [
    {
      "source_table": "equipment",
      "row_pk": "AHU-017",
      "confidence": 0.49,
      "match_method": "exact_key",
      "row_data": {
        "equipment_id": "AHU-017",
        "equipment_name": "Main AHU - North Wing",
        "building": "Building A",
        "floor": 5,
        "manufacturer": "Trane",
        "model": "CGAM-100"
      },
      "evidence": "equipment_id: AHU-017. equipment_name: Main AHU North Wing..."
    }
  ],
  "latency_ms": 423,
  "model_name": "mock-answer"
}
```

---

## Step 5: Row-Only Query (No LLM Answer)

Get matched rows without generating an answer (faster, cheaper):

```bash
curl -X POST http://localhost:8000/rag/rows \
  -H "Content-Type: application/json" \
  -d '{
    "query": "elevator equipment",
    "top_k": 10
  }'
```

**Expected response:**
```json
{
  "query": "elevator equipment",
  "query_type": "factual",
  "matched_rows": [
    {
      "source_table": "elevators",
      "row_pk": "EL-001",
      "confidence": 0.49,
      "match_method": "exact_key",
      "row_data": {
        "elevator_id": "EL-001",
        "elevator_name": "Main Elevator - North Wing",
        "building": "Building A",
        "total_load_kw": 45,
        "manufacturer": "Otis"
      },
      "evidence": "elevator_id: EL-001..."
    }
  ],
  "latency_ms": 87,
  "total_chunks_searched": 8,
  "unique_rows_matched": 1
}
```

---

## Additional Endpoints

### List all documents
```bash
curl http://localhost:8000/documents
```

### Get document details
```bash
curl http://localhost:8000/documents/{document_id}
```

### View document chunks
```bash
curl http://localhost:8000/documents/{document_id}/chunks?limit=5
```

### Delete a document
```bash
curl -X DELETE http://localhost:8000/documents/{document_id}
```

### Health check
```bash
curl http://localhost:8000/health
```

### Check operational modes
```bash
curl http://localhost:8000/
```

---

## Query Parameters Reference

### POST /documents/{id}/match-rows
- `confidence_threshold` (float, default 0.3): Minimum match confidence (0.0-1.0)
- `group_by_table` (bool, default true): Group results by source_table

### GET /documents/{id}/match-rows/debug
- `show_all_chunks` (bool, default false): Include chunks with no matches
- `confidence_threshold` (float, default 0.0): Filter matches below this score

### POST /rag/query
- `query` (string, required): Natural language question
- `top_k` (int, default 8): Number of chunks to retrieve
- `filters` (object, optional): Filter by document metadata
- `user_id` (string, optional): For audit logging
- `session_id` (string, optional): For conversation tracking

### POST /rag/rows
- Same as /rag/query but returns only matched rows, no LLM answer

---

## Expected Match Methods

| Method | Meaning | Example |
|--------|---------|---------|
| `exact_key` | Asset code found in both chunk and row | "AHU-017" appears in both |
| `normalized_key` | Asset code with normalization | "AHU 017" ↔ "AHU-017" |
| `semantic` | Embedding similarity | Descriptions are semantically similar |
| `keyword` | Token overlap | Multiple keywords match |

---

## Troubleshooting

**Problem**: `unique_rows_matched: 0`
- **Cause**: Row index is empty or asset codes don't match
- **Fix**: Run `python -m scripts.test_document_matching` or seed your real data

**Problem**: Low confidence scores (< 0.3)
- **Cause**: No OPENAI_API_KEY (embeddings in mock mode)
- **Fix**: Set the API key in .env and restart

**Problem**: Wrong rows matching
- **Cause**: Confidence threshold too low
- **Fix**: Raise to 0.5: `"confidence_threshold": 0.5`

**Problem**: Missing expected matches
- **Cause**: Asset codes not in row `semantic_text`
- **Fix**: Include PK column when seeding: `equipment_id: AHU-017. equipment_name: ...`

---

## Next Steps

1. **Seed your real asset data:**
   ```bash
   python -m scripts.seed_row_index --table equipment --csv your_data.csv
   ```

2. **Upload your real documents and test matching**

3. **Adjust confidence_threshold** based on your validation results

4. **Integrate into your application** using these same API calls
