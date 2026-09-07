# Row-by-Row Iteration API

New endpoint that iterates through **each database row** and shows which document chunks matched.

This is the **inverse** of the normal matching approach - instead of going chunk-by-chunk, this goes **row-by-row**.

---

## API Endpoints

### 1. Full Row Iteration (with complete chunk text)

```bash
POST /rows/{document_id}/iterate-rows
```

**Request:**
```bash
curl -X POST http://localhost:8000/rows/896c64dd-8bc2-4570-9d60-866783bf7677/iterate-rows \
  -H "Content-Type: application/json" \
  -d '{
    "confidence_threshold": 0.15
  }'
```

**Response:**
```json
{
  "document_id": "896c64dd-8bc2-4570-9d60-866783bf7677",
  "file_name": "Green-Line-Project.pdf",
  "total_rows_checked": 30,
  "rows_with_matches": 15,
  "rows_without_matches": 15,
  "confidence_threshold": 0.15,
  
  "iterations": [
    {
      "row_index": 0,
      "source_table": "assets",
      "row_pk": "ESC-001",
      
      "row_data": {
        "asset_code": "ESC-001",
        "asset_name": "Main Escalator Lobby",
        "manufacturer": "Schindler",
        "model": "9300",
        "building": "Building A",
        "floor": "Ground",
        ...all CSV columns...
      },
      
      "has_match": true,
      "best_confidence": 0.58,
      "total_chunks_matched": 2,
      "match_summary": "Found on pages 12, 20",
      
      "matched_chunks": [
        {
          "chunk_id": "chunk-abc-123",
          "chunk_index": 45,
          "page_number": 12,
          "block_type": "paragraph",
          "confidence": 0.58,
          "matched_fields": [
            "manufacturer=Schindler",
            "model=9300",
            "floor=Ground"
          ],
          "match_details": {
            "semantic_score": 0.72,
            "bm25_overlap": 0.45,
            "metadata_overlap": 0.60
          },
          "chunk_text": "The Schindler 9300 escalator on the ground floor of Building A requires quarterly maintenance as per manufacturer specifications. The unit has been operational since 2019 and is located at the Main Entrance serving the lobby area..."
        },
        {
          "chunk_id": "chunk-abc-156",
          "chunk_index": 78,
          "page_number": 20,
          "confidence": 0.42,
          "matched_fields": [
            "location=Main Entrance",
            "building=Building A"
          ],
          "chunk_text": "Main Entrance escalator in Building A lobby was inspected and found to be in operational condition..."
        }
      ]
    },
    
    {
      "row_index": 1,
      "source_table": "assets",
      "row_pk": "AHU-001",
      "row_data": {
        "asset_code": "AHU-001",
        "asset_name": "Main Air Handler Unit North",
        ...
      },
      "has_match": true,
      "best_confidence": 0.52,
      "total_chunks_matched": 1,
      "match_summary": "Found on pages 9",
      "matched_chunks": [...]
    },
    
    {
      "row_index": 2,
      "source_table": "assets",
      "row_pk": "LIGHT-001",
      "row_data": {
        "asset_code": "LIGHT-001",
        "asset_name": "Exterior Lighting Control",
        ...
      },
      "has_match": false,
      "best_confidence": 0.0,
      "total_chunks_matched": 0,
      "match_summary": "No matches found",
      "matched_chunks": []
    }
  ]
}
```

---

### 2. Summary View (without full chunk text)

```bash
POST /rows/{document_id}/iterate-rows/summary
```

**Request:**
```bash
curl -X POST http://localhost:8000/rows/896c64dd-8bc2-4570-9d60-866783bf7677/iterate-rows/summary \
  -H "Content-Type: application/json" \
  -d '{
    "confidence_threshold": 0.15,
    "show_unmatched": false
  }'
```

**Parameters:**
- `confidence_threshold`: 0.15 (default) - minimum confidence to include
- `show_unmatched`: false (default) - set true to see rows with no matches

**Response:**
```json
{
  "document_id": "...",
  "total_rows_checked": 30,
  "rows_with_matches": 15,
  "rows_without_matches": 15,
  "note": "Unmatched rows hidden",
  
  "iterations": [
    {
      "row_index": 0,
      "row_pk": "ESC-001",
      "row_data": {...},
      "has_match": true,
      "best_confidence": 0.58,
      "total_chunks_matched": 2,
      "match_summary": "Found on pages 12, 20",
      
      "matched_chunks": [
        {
          "chunk_index": 45,
          "page_number": 12,
          "confidence": 0.58,
          "matched_fields": ["manufacturer=Schindler", "model=9300"],
          "chunk_text_preview": "The Schindler 9300 escalator on the ground floor..."
        }
      ]
    }
  ]
}
```

---

## Use Cases

### Use Case 1: Asset Coverage Report

Find which assets from your database are mentioned in the document:

```javascript
const result = await fetch('/rows/{doc_id}/iterate-rows/summary', {
  method: 'POST',
  body: JSON.stringify({ show_unmatched: false })
});

console.log(`Coverage: ${result.rows_with_matches}/${result.total_rows_checked} assets mentioned`);

result.iterations.forEach(row => {
  console.log(`${row.row_pk}: ${row.match_summary}`);
});
```

**Output:**
```
Coverage: 15/30 assets mentioned

ESC-001: Found on pages 12, 20
AHU-001: Found on pages 9
CHW-001: Found on pages 15, 22, 34
...
```

---

### Use Case 2: Missing Assets Report

Find which assets are NOT mentioned:

```javascript
const result = await fetch('/rows/{doc_id}/iterate-rows/summary', {
  method: 'POST',
  body: JSON.stringify({ show_unmatched: true })
});

const missing = result.iterations.filter(r => !r.has_match);

console.log(`\nAssets NOT mentioned in document:`);
missing.forEach(row => {
  console.log(`  - ${row.row_pk}: ${row.row_data.asset_name}`);
});
```

**Output:**
```
Assets NOT mentioned in document:
  - LIGHT-001: Exterior Lighting Control System
  - SOFT-001: Water Softener System
  - COMP-002: Air Compressor Backup
```

---

### Use Case 3: Citation Extraction per Asset

Get all document citations for a specific asset:

```javascript
const result = await fetch('/rows/{doc_id}/iterate-rows');
const asset = result.iterations.find(r => r.row_pk === 'ESC-001');

console.log(`\nAsset: ${asset.row_pk} - ${asset.row_data.asset_name}`);
console.log(`Total mentions: ${asset.total_chunks_matched}`);
console.log(`\nCitations:`);

asset.matched_chunks.forEach(chunk => {
  console.log(`\nPage ${chunk.page_number}:`);
  console.log(`  Fields found: ${chunk.matched_fields.join(', ')}`);
  console.log(`  "${chunk.chunk_text}"`);
});
```

**Output:**
```
Asset: ESC-001 - Main Escalator Lobby
Total mentions: 2

Citations:

Page 12:
  Fields found: manufacturer=Schindler, model=9300, floor=Ground
  "The Schindler 9300 escalator on the ground floor..."

Page 20:
  Fields found: location=Main Entrance, building=Building A
  "Main Entrance escalator in Building A lobby was inspected..."
```

---

### Use Case 4: Quality Analysis

Analyze match quality for each asset:

```javascript
const result = await fetch('/rows/{doc_id}/iterate-rows/summary');

console.log('Match Quality Analysis:\n');

result.iterations
  .filter(r => r.has_match)
  .sort((a, b) => b.best_confidence - a.best_confidence)
  .forEach(row => {
    const quality = row.best_confidence > 0.5 ? 'HIGH' : 
                    row.best_confidence > 0.3 ? 'MEDIUM' : 'LOW';
    
    console.log(`${row.row_pk}: ${quality} (${row.best_confidence})`);
    console.log(`  Chunks: ${row.total_chunks_matched}`);
    console.log(`  ${row.match_summary}\n`);
  });
```

**Output:**
```
Match Quality Analysis:

ESC-001: HIGH (0.58)
  Chunks: 2
  Found on pages 12, 20

AHU-001: HIGH (0.52)
  Chunks: 1
  Found on pages 9

CT-001: MEDIUM (0.38)
  Chunks: 1
  Found on pages 15
```

---

## Comparison: Chunk-Centric vs Row-Centric

### Chunk-Centric (Original)
```
POST /documents/{doc_id}/match-rows
```
- Iterates through **document chunks**
- Shows which database rows matched each chunk
- Good for: "What's in this document?"

### Row-Centric (New)
```
POST /rows/{doc_id}/iterate-rows
```
- Iterates through **database rows**
- Shows which document chunks matched each row
- Good for: "Which of my assets are mentioned?"

---

## Performance

**For 30 rows × 500 chunks:**
- `/iterate-rows` (full): ~10-15 seconds, 5-10 MB response
- `/iterate-rows/summary`: ~10-15 seconds, 500 KB response

**Optimization tips:**
- Use `/summary` endpoint for large documents
- Set `show_unmatched: false` to reduce response size
- Filter by `source_table` if you only care about specific tables

---

## Example: Complete Workflow

```bash
# 1. Upload document
curl -X POST http://localhost:8000/documents/upload \
  -F "file=@contract.pdf"
# Returns: {"document_id": "abc-123"}

# 2. Iterate through your assets
curl -X POST http://localhost:8000/rows/abc-123/iterate-rows/summary \
  -d '{"confidence_threshold": 0.15, "show_unmatched": false}'

# Response shows:
# - Which assets were found
# - Where they were mentioned
# - What metadata fields matched
# - Match quality scores
```

---

## Summary

✅ **Row-by-row iteration** through your database  
✅ **Shows all chunks** that matched each row  
✅ **Metadata field tracking** - see what matched  
✅ **Page citations** - know where assets are mentioned  
✅ **Coverage reporting** - find missing assets  
✅ **Quality scores** - validate match strength  

Perfect for:
- Asset coverage reports
- Citation extraction
- Missing asset detection
- Quality validation
- Compliance verification
