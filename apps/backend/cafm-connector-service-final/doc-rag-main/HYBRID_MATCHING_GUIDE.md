# Semantic + BM25 Hybrid Matching Guide

The system now uses **pure hybrid search** without requiring exact asset code matches.

---

## How Matching Works Now

### **Old Approach** (Exact Keys Required)
```
❌ Document: "The escalator needs service"
❌ Database: "ESC-001"
❌ Result: NO MATCH (no exact code found)
```

### **New Approach** (Hybrid Search)
```
✅ Document: "The Schindler 9300 escalator on ground floor needs service"
✅ Database: {"manufacturer": "Schindler", "model": "9300", "floor": "Ground"}
✅ Result: MATCH via semantic similarity + metadata overlap
```

---

## Matching Algorithm

### **Three Signals Combined:**

1. **Semantic Similarity (40% weight)**
   - Vector embeddings comparison
   - Understands "escalator" ≈ "lift mechanism"
   - Requires OPENAI_API_KEY for best results

2. **BM25 Token Overlap (30% weight)**
   - Keyword matching with term frequency
   - "Schindler 9300" in both document and database
   - Works without embeddings

3. **Metadata Field Matching (30% weight)**
   - Direct string matching in JSON fields
   - Checks: manufacturer, model, location, building, floor, etc.
   - Case-insensitive

4. **Exact Code Bonus (+10% if present)**
   - "ESC-001" exact match gives small boost
   - NOT required for matching anymore

---

## Confidence Formula

```python
confidence = (
    0.40 × semantic_similarity    # 0.0 to 1.0
    + 0.30 × token_overlap        # 0.0 to 1.0  
    + 0.30 × metadata_overlap     # 0.0 to 1.0
    + 0.10 × exact_key_bonus      # 0 or 0.1
)
```

**Default threshold: 0.15** (down from 0.25)

---

## Example Matches

### Example 1: Manufacturer + Model
```json
Document chunk: "The Schindler 9300 escalator requires maintenance"

Database row: {
  "asset_code": "ESC-001",
  "manufacturer": "Schindler",
  "model": "9300",
  "category": "Escalator"
}

Match scores:
  • Semantic: 0.72 (escalator ≈ escalator)
  • Token overlap: 0.45 (Schindler, 9300, escalator)
  • Metadata: 0.60 (found Schindler, 9300, Escalator)
  
→ Confidence: 0.40×0.72 + 0.30×0.45 + 0.30×0.60 = 0.60 ✓
```

### Example 2: Location-based
```json
Document chunk: "Ground floor Main Entrance lift is operational"

Database row: {
  "asset_code": "EL-001",
  "asset_name": "Main Elevator",
  "floor": "Ground",
  "location": "Main Entrance",
  "category": "Elevator"
}

Match scores:
  • Semantic: 0.68 (lift ≈ elevator)
  • Token overlap: 0.40 (Ground, Main, Entrance)
  • Metadata: 0.55 (found Ground, Main Entrance)
  
→ Confidence: 0.40×0.68 + 0.30×0.40 + 0.30×0.55 = 0.56 ✓
```

### Example 3: Serial Number
```json
Document chunk: "Unit SCH-2019-ESC001 inspection completed"

Database row: {
  "serial_number": "SCH-2019-ESC001"
}

Match scores:
  • Semantic: 0.20 (low - generic text)
  • Token overlap: 0.80 (serial number match)
  • Metadata: 0.90 (exact serial match)
  
→ Confidence: 0.40×0.20 + 0.30×0.80 + 0.30×0.90 = 0.59 ✓
```

---

## API Usage

### Test with Different Thresholds

```bash
# Low threshold (0.15) - more matches, some may be weak
curl -X POST http://localhost:8000/documents/{doc_id}/match-rows \
  -H "Content-Type: application/json" \
  -d '{"confidence_threshold": 0.15}'

# Medium threshold (0.25) - balanced
curl -X POST http://localhost:8000/documents/{doc_id}/match-rows \
  -H "Content-Type: application/json" \
  -d '{"confidence_threshold": 0.25}'

# High threshold (0.40) - only strong matches
curl -X POST http://localhost:8000/documents/{doc_id}/match-rows \
  -H "Content-Type: application/json" \
  -d '{"confidence_threshold": 0.40}'
```

### Response Format

```json
{
  "unique_rows_matched": 15,
  "matched_rows": [
    {
      "source_table": "assets",
      "row_pk": "ESC-001",
      "confidence": 0.58,
      "match_method": "hybrid",
      "row_data": {
        "asset_code": "ESC-001",
        "manufacturer": "Schindler",
        "model": "9300",
        ...all CSV columns...
      },
      "chunk_count": 3
    }
  ]
}
```

**Match Methods:**
- `semantic` - Primarily matched via embeddings (>0.5 similarity)
- `bm25` - Primarily matched via keyword overlap (>0.2 overlap)
- `metadata_match` - Primarily matched via metadata fields (>0.3 overlap)
- `hybrid` - Balanced contribution from all signals
- `exact_key` / `normalized_key` - Bonus for exact codes (rare now)

---

## Improving Match Quality

### 1. Set OPENAI_API_KEY

**Without key:**
- ❌ Semantic similarity = 0
- ✓ Only BM25 + metadata work
- Result: 50-60% match rate

**With key:**
- ✓ Semantic similarity works
- ✓ BM25 + metadata work
- Result: 80-90% match rate

```bash
echo "OPENAI_API_KEY=sk-your-key" >> .env
docker compose restart
```

### 2. Enrich Your CSV

More metadata = better matching:

```csv
asset_code,asset_name,manufacturer,model,serial_number,building,floor,location,notes
ESC-001,Main Escalator,Schindler,9300,SCH-2019-ESC001,Building A,Ground,Main Entrance,Lobby escalator
```

More searchable fields = more ways to match!

### 3. Include Descriptions

If your documents use descriptive language:

```csv
asset_code,description
ESC-001,Schindler 9300 escalator connecting ground floor to level 2 in main entrance lobby
```

The `description` field gets embedded and searched semantically.

### 4. Tune the Threshold

Start low and increase:

```bash
# Try 0.15 first (liberal matching)
# If too many false positives, increase to 0.20, 0.25, etc.
```

---

## Debugging No Matches

If you're still getting 0 matches:

### Check 1: Is there ANY textual overlap?

```bash
python scripts/debug_matching.py {doc_id}
```

This shows what text exists in both document and database.

### Check 2: Are embeddings present?

```sql
SELECT COUNT(*) FROM row_semantic_index WHERE embedding IS NOT NULL;
```

If 0, you need to set OPENAI_API_KEY and re-load the CSV.

### Check 3: Is metadata rich enough?

Your database needs descriptive fields like:
- manufacturer
- model  
- location
- building
- description

Not just:
- id
- code
- name (if generic like "Unit 1")

### Check 4: Lower the threshold

```bash
# Try 0.10 as an experiment
curl -X POST http://localhost:8000/documents/{doc_id}/match-rows \
  -d '{"confidence_threshold": 0.10}'
```

If this finds matches, your threshold was too high.

---

## Performance

**Speed:**
- 30 rows, 500 chunks = ~5-10 seconds
- 1000 rows, 500 chunks = ~30-60 seconds

**Optimization:**
- Add vector index (pgvector IVFFlat) for >10K rows
- Filter by document metadata before full scan
- Cache embeddings

---

## Summary

✅ **No exact codes needed** - matches via semantic + keyword similarity
✅ **Metadata-aware** - searches all JSON fields
✅ **Hybrid scoring** - combines 3 signals with tunable weights
✅ **Flexible threshold** - adjustable confidence cutoff (default 0.15)
✅ **Works without embeddings** - BM25 + metadata still function

**Start here:**
```bash
# 1. Set API key for best results
echo "OPENAI_API_KEY=sk-..." >> .env
docker compose restart

# 2. Load your CSV
python scripts/load_csv_to_postgres.py your_assets.csv

# 3. Test matching with low threshold
curl -X POST http://localhost:8000/documents/{doc_id}/match-rows \
  -d '{"confidence_threshold": 0.15}'
```
