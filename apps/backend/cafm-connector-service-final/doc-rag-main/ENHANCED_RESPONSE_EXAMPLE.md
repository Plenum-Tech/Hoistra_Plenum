# Enhanced Match Response - Example

This shows what the API returns with the new detailed matching information.

## API Call

```bash
curl -X POST http://localhost:8000/documents/{doc_id}/match-rows \
  -H "Content-Type: application/json" \
  -d '{"confidence_threshold": 0.15, "group_by_table": true}'
```

## Response Structure

```json
{
  "document_id": "896c64dd-8bc2-4570-9d60-866783bf7677",
  "file_name": "Green-Line-Project-Facility-Management-Agreement_compressed.pdf",
  "total_chunks_analyzed": 471,
  "unique_rows_matched": 15,
  "matched_rows": [
    {
      "source_table": "assets",
      "row_pk": "ESC-001",
      "confidence": 0.58,
      "match_method": "hybrid",
      
      // Full database row data (all CSV columns)
      "row_data": {
        "asset_code": "ESC-001",
        "asset_name": "Main Escalator Lobby",
        "category": "Escalator",
        "building": "Building A",
        "floor": "Ground",
        "location": "Main Entrance",
        "manufacturer": "Schindler",
        "model": "9300",
        "serial_number": "SCH-2019-ESC001",
        "install_date": "2019-04-10",
        "status": "Operational",
        "criticality": "Medium"
      },
      
      // NEW: Which metadata fields were found in the document
      "matched_metadata_fields": [
        "manufacturer=Schindler",
        "model=9300",
        "building=Building A",
        "floor=Ground",
        "location=Main Entrance"
      ],
      
      // NEW: Score breakdown showing how the match was calculated
      "match_details": {
        "semantic_score": 0.72,      // Embedding similarity (0-1)
        "bm25_overlap": 0.45,         // Keyword overlap (0-1)
        "metadata_overlap": 0.60,     // % of metadata fields found
        "exact_key_match": false,     // Was exact code found?
        "normalized_key_match": false // Was normalized code found?
      },
      
      // NEW: Details about each chunk that matched this row
      "chunk_matches": [
        {
          "chunk_id": "chunk-abc-123",
          "chunk_index": 45,
          "page_number": 12,
          "confidence": 0.58,
          "matched_fields": [
            "manufacturer=Schindler",
            "model=9300",
            "floor=Ground"
          ],
          "chunk_text_preview": "The Schindler 9300 escalator on the ground floor of Building A requires quarterly maintenance as per manufacturer specifications..."
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
          "chunk_text_preview": "Main Entrance escalator in Building A lobby was inspected and found to be in operational condition..."
        }
      ],
      
      "evidence": "asset_code: ESC-001. asset_name: Main Escalator Lobby. category: Escalator. building: Building A...",
      "chunk_ids": ["chunk-abc-123", "chunk-abc-156"],
      "chunk_count": 2
    },
    
    {
      "source_table": "assets",
      "row_pk": "AHU-001",
      "confidence": 0.52,
      "match_method": "metadata_match",
      "row_data": {
        "asset_code": "AHU-001",
        "asset_name": "Main Air Handler Unit North",
        "category": "HVAC",
        "building": "Building A",
        "floor": "5",
        "location": "Mechanical Room 501",
        "manufacturer": "Trane",
        "model": "CGAM-100"
      },
      "matched_metadata_fields": [
        "manufacturer=Trane",
        "model=CGAM-100",
        "building=Building A",
        "category=HVAC"
      ],
      "match_details": {
        "semantic_score": 0.65,
        "bm25_overlap": 0.38,
        "metadata_overlap": 0.50,
        "exact_key_match": false,
        "normalized_key_match": false
      },
      "chunk_matches": [
        {
          "chunk_id": "chunk-abc-89",
          "chunk_index": 34,
          "page_number": 9,
          "confidence": 0.52,
          "matched_fields": [
            "manufacturer=Trane",
            "model=CGAM-100",
            "category=HVAC"
          ],
          "chunk_text_preview": "HVAC equipment maintenance schedule includes the Trane CGAM-100 air handling units located in Building A mechanical rooms..."
        }
      ],
      "chunk_ids": ["chunk-abc-89"],
      "chunk_count": 1
    }
  ],
  
  "by_table": {
    "assets": 15
  },
  
  "latency_ms": 7845
}
```

## Key Features

### 1. **matched_metadata_fields**
Shows exactly which fields from your CSV matched:
```json
"matched_metadata_fields": [
  "manufacturer=Schindler",
  "model=9300",
  "building=Building A"
]
```

### 2. **match_details**
Score breakdown showing how confidence was calculated:
```json
"match_details": {
  "semantic_score": 0.72,     // 40% weight
  "bm25_overlap": 0.45,        // 30% weight
  "metadata_overlap": 0.60,    // 30% weight
  "exact_key_match": false
}
```

Formula: `0.40×0.72 + 0.30×0.45 + 0.30×0.60 = 0.603`

### 3. **chunk_matches**
Every document chunk that matched this row:
```json
"chunk_matches": [
  {
    "chunk_index": 45,
    "page_number": 12,
    "matched_fields": ["manufacturer=Schindler", "model=9300"],
    "chunk_text_preview": "The Schindler 9300 escalator..."
  }
]
```

You can see:
- Which page mentioned this asset
- What parts of the chunk matched
- Preview of the actual text

## Use Cases

### Find where assets are mentioned
```javascript
// Get all chunks that mention asset ESC-001
const asset = response.matched_rows.find(r => r.row_pk === "ESC-001");
asset.chunk_matches.forEach(chunk => {
  console.log(`Page ${chunk.page_number}: ${chunk.chunk_text_preview}`);
});
```

### Analyze match quality
```javascript
// See why this matched
const match = response.matched_rows[0];
console.log(`Confidence: ${match.confidence}`);
console.log(`Method: ${match.match_method}`);
console.log(`Semantic: ${match.match_details.semantic_score}`);
console.log(`BM25: ${match.match_details.bm25_overlap}`);
console.log(`Metadata: ${match.match_details.metadata_overlap}`);
console.log(`Fields matched: ${match.matched_metadata_fields.join(', ')}`);
```

### Generate report
```javascript
// Create citation report
match.chunk_matches.forEach(chunk => {
  console.log(`
    Asset: ${match.row_pk}
    Page: ${chunk.page_number}
    Fields found: ${chunk.matched_fields.join(', ')}
    Quote: "${chunk.chunk_text_preview}"
  `);
});
```

## Benefits

✅ **Transparency**: See exactly why each match occurred
✅ **Debugging**: Identify false positives/negatives
✅ **Citations**: Know which pages mention each asset
✅ **Validation**: Verify metadata fields are being used
✅ **Tuning**: Adjust weights based on score breakdown
