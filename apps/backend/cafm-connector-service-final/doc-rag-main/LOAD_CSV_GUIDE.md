# Quick Start: Load Your Assets CSV

This guide shows how to load your assets CSV directly into the system.

## One-Command Load

```bash
python scripts/load_csv_to_index.py your_assets.csv
```

That's it! The script will:
1. ✓ Read your CSV
2. ✓ Create embeddings for each row
3. ✓ Insert into `row_semantic_index` table
4. ✓ Verify the data was loaded

---

## If Your CSV Has Different Column Names

### Example 1: PK column is named "Equipment ID"

```bash
python scripts/load_csv_to_index.py equipment.csv \
  --table equipment \
  --pk "Equipment ID"
```

### Example 2: Only include specific columns

```bash
python scripts/load_csv_to_index.py assets.csv \
  --pk "Asset Code" \
  --columns "Asset Code,Asset Name,Building,Floor,Category"
```

---

## Sample CSV Format

Your CSV should look like this:

```csv
asset_code,asset_name,category,building,floor,location,manufacturer,model,status
AHU-001,Main Air Handler,HVAC,Building A,5,Room 501,Trane,CGAM-100,Operational
AHU-002,Service Air Handler,HVAC,Building B,3,Room 301,Carrier,39M-100,Operational
EL-001,Main Elevator,Elevator,Building A,Ground,Lobby,Otis,Gen2,Operational
```

**Important**: 
- First row must be headers
- Must have a column to use as primary key (like `asset_code`)
- All columns are automatically stored in the database

---

## Using the Sample Data

We've provided sample data you can test with:

```bash
python scripts/load_csv_to_index.py sample_data/assets.csv
```

This loads 30 sample assets (HVAC, elevators, pumps, etc.)

---

## Verify It Worked

After loading, check the database:

```bash
python -c "
from app.db.session import SessionLocal
from app.db.models import RowSemanticIndex

db = SessionLocal()
count = db.query(RowSemanticIndex).count()
print(f'Total rows in index: {count}')

# Show first 5
rows = db.query(RowSemanticIndex).limit(5).all()
for r in rows:
    print(f'  {r.source_table}.{r.row_pk}: {r.semantic_text[:60]}...')
"
```

---

## Common Issues

### Issue: "Column 'asset_code' not found"

**Cause**: Your CSV uses a different column name for the primary key.

**Fix**: Specify the correct column name:
```bash
python scripts/load_csv_to_index.py your_file.csv --pk "Your PK Column Name"
```

### Issue: "No valid rows to insert"

**Cause**: Your PK column has empty values.

**Fix**: Make sure every row in your CSV has a value in the primary key column.

### Issue: "Using MOCK embeddings"

**Cause**: `OPENAI_API_KEY` is not set.

**Effect**: The system will still work, but semantic matching will be limited to exact text matches.

**Fix**: Set your API key in `.env`:
```bash
echo "OPENAI_API_KEY=sk-your-key-here" >> .env
docker compose restart
```

---

## Next Steps

After loading your CSV:

1. **Upload a document** that mentions your assets:
   ```bash
   curl -X POST http://localhost:8000/documents/upload \
     -F "file=@maintenance_report.pdf"
   ```

2. **Match document to assets**:
   ```bash
   curl -X POST http://localhost:8000/documents/{doc_id}/match-rows
   ```

3. **Check the matched_rows** in the response - you should see your CSV data!

---

## Script Options

```
python scripts/load_csv_to_index.py CSV_FILE [OPTIONS]

Required:
  CSV_FILE              Path to your CSV file

Options:
  --table NAME          Table name (default: assets)
  --pk COLUMN           Primary key column (default: asset_code)
  --columns LIST        Comma-separated columns to include (default: all)

Examples:
  python scripts/load_csv_to_index.py data.csv
  python scripts/load_csv_to_index.py data.csv --pk "Equipment ID"
  python scripts/load_csv_to_index.py data.csv --table equipment --columns "id,name,location"
```

---

## What Gets Stored

For each CSV row, the script creates:

| Field | Content |
|-------|---------|
| `source_table` | Your table name (e.g. "assets") |
| `row_pk` | Value from your PK column (e.g. "AHU-001") |
| `semantic_text` | Searchable text: "asset_code: AHU-001. asset_name: Main Air Handler. building: A..." |
| `embedding` | Vector embedding for semantic search |
| `meta` | JSON with ALL your CSV columns |

When the RAG system matches a document to an asset, you get back the full `meta` JSON with all your CSV columns!
