# Quick Reference - Document to Rows Matching API

## One-Command Test

```bash
# Run the full automated test suite
bash scripts/test_api.sh
```

## Manual Testing (3 steps)

### 1. Upload Document
```bash
curl -X POST http://localhost:8000/documents/upload \
  -F "file=@your_document.pdf"
# Returns: {"document_id": "abc-123", ...}
```

### 2. Match Document to Rows
```bash
curl -X POST http://localhost:8000/documents/abc-123/match-rows \
  -H "Content-Type: application/json" \
  -d '{"confidence_threshold": 0.3}'
# Returns: {"unique_rows_matched": 12, "matched_rows": [...], ...}
```

### 3. Debug (Optional)
```bash
curl "http://localhost:8000/documents/abc-123/match-rows/debug?show_all_chunks=true"
# Returns: {"chunk_details": [...], "chunks_with_matches": 89, ...}
```

## Key Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/documents/upload` | POST | Upload PDF/DOCX/TXT |
| `/documents/{id}/match-rows` | POST | Match document to database rows |
| `/documents/{id}/match-rows/debug` | GET | Chunk-by-chunk analysis |
| `/rag/query` | POST | Natural language query + rows |
| `/rag/rows` | POST | Row search without LLM answer |
| `/documents` | GET | List all documents |
| `/health` | GET | Check API status |

## Response Fields

Every matched row includes:
- `source_table` - your database table name (e.g. "equipment")
- `row_pk` - primary key value (e.g. "AHU-017")
- `confidence` - match score 0.0-1.0
- `match_method` - exact_key | normalized_key | semantic | keyword
- `row_data` - **full database row with all columns**
- `evidence` - chunk text that triggered the match
- `chunk_ids` - which chunks matched this row
- `chunk_count` - how many chunks mentioned this row

## Confidence Threshold Tuning

| Value | Use Case |
|-------|----------|
| 0.25-0.30 | Permissive - catch all possible matches |
| 0.40-0.50 | Balanced - recommended for production |
| 0.60+ | Strict - only very confident matches |

## Before Testing

1. **Start API**: `docker compose up -d`
2. **Seed test data**: `python -m scripts.test_document_matching`
3. **Verify**: `curl http://localhost:8000/health`

## Full Documentation

- Complete guide: `API_TESTING_GUIDE.md`
- Seeding your data: `README.md` → "Row-level grounding"
- Troubleshooting: `README.md` → "Testing document-to-rows matching"
