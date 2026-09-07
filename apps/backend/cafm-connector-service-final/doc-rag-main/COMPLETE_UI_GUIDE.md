# Complete RAG Platform UI

**All-in-one interface** for the complete workflow: CSV upload → Document upload → Row matching

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install -r streamlit_requirements.txt

# 2. Start API
docker compose up -d

# 3. Launch UI
bash run_ui.sh

# OR directly:
streamlit run complete_ui.py
```

**Access at:** http://localhost:8501

---

## ✨ Features

### **Step 1: Upload Assets CSV** 📊
- Drag & drop CSV file
- Preview data before upload
- Select primary key column
- Specify table name
- Loads into `row_semantic_index`

### **Step 2: Upload Document** 📄
- Upload PDF, DOCX, or TXT
- Automatic chunking
- Stores in `document_chunks`
- Vision-based table extraction

### **Step 3: Match Assets** 🔍
- Row-by-row iteration matching
- Adjustable confidence threshold
- Show/hide unmatched assets
- Real-time results

### **View Results** 📊
- **All Results Tab:**
  - Color-coded by confidence
  - Expandable asset cards
  - Full metadata display
  - Matched field badges
  - Document citations with page numbers
  - Chunk text previews
  
- **Statistics Tab:**
  - Match distribution charts
  - Confidence breakdown
  - Top 10 matches
  - Field usage analysis
  
- **Export Tab:**
  - JSON download (complete data)
  - CSV download (flat table)

---

## 📋 Workflow

```
┌─────────────────────────────────────────┐
│  Step 1: Upload CSV                     │
│  ┌────────────────────────────────┐    │
│  │ • Drag CSV file                │    │
│  │ • Select PK column             │    │
│  │ • Click "Load to Database"     │    │
│  └────────────────────────────────┘    │
│         ↓                               │
│  Step 2: Upload Document                │
│  ┌────────────────────────────────┐    │
│  │ • Drag PDF/DOCX/TXT file       │    │
│  │ • Click "Process Document"     │    │
│  │ • Wait for chunking            │    │
│  └────────────────────────────────┘    │
│         ↓                               │
│  Step 3: Match Assets                   │
│  ┌────────────────────────────────┐    │
│  │ • Click "Run Matching"         │    │
│  │ • View results                 │    │
│  │ • Analyze statistics           │    │
│  │ • Export data                  │    │
│  └────────────────────────────────┘    │
└─────────────────────────────────────────┘
```

---

## 🎨 Result Display

### Matched Asset (High Confidence)
```
✅ ESC-001 - Main Escalator Lobby (Confidence: 0.580)

📊 Asset Details          │ 📈 Match Info
─────────────────────────┼────────────────
asset_code    ESC-001    │ Found on pages 12, 20
asset_name    Main Esc   │ Confidence: 0.580
manufacturer  Schindler  │ Chunks: 2
model         9300       │
building      Building A │
floor         Ground     │

📄 Document Citations

Citation 1 - Page 12, Chunk 45
Matched Fields: [manufacturer=Schindler] [model=9300] [floor=Ground]
Text Preview: "The Schindler 9300 escalator on the ground floor..."

Citation 2 - Page 20, Chunk 78
Matched Fields: [location=Main Entrance] [building=Building A]
Text Preview: "Main Entrance escalator in Building A lobby..."
```

### Unmatched Asset
```
❌ LIGHT-001 - Exterior Lighting Control (No Match)

📊 Asset Details
asset_code    LIGHT-001
asset_name    Exterior Lighting
category      Lighting

📈 Match Info
No matches found
```

---

## ⚙️ Settings (Sidebar)

### Confidence Threshold
- **Range:** 0.0 to 1.0
- **Default:** 0.15
- **Higher:** Fewer, higher-quality matches
- **Lower:** More matches, may include weak ones

### Show Unmatched Assets
- **ON:** Show all assets (matched + unmatched)
- **OFF:** Only show matched assets

### Workflow Status
- ✅ CSV Loaded
- ✅ Document Uploaded
- ✅ Matching Done

---

## 📊 API Calls Made

### Step 1: CSV Upload
**Note:** Currently stores in session state. For production, you'd add a CSV upload endpoint to the API:
```
POST /assets/upload-csv
```

### Step 2: Document Upload
```
POST /documents/upload
FormData: file=document.pdf
```

**Response:**
```json
{
  "document_id": "a70f7956-13da-4b6a-8af7-5a764f2cdb9e",
  "status": "indexed",
  "num_chunks": 6
}
```

### Step 3: Row Matching
```
POST /rows/{document_id}/iterate-rows/summary
{
  "confidence_threshold": 0.15,
  "show_unmatched": false
}
```

**Response:**
```json
{
  "total_rows_checked": 30,
  "rows_with_matches": 4,
  "rows_without_matches": 26,
  "iterations": [...]
}
```

---

## 🎯 Example Usage

### Complete Example

```bash
# 1. Start services
docker compose up -d

# 2. Launch UI
streamlit run complete_ui.py

# 3. In browser (http://localhost:8501):

# Step 1: Upload CSV
#   - Click "Upload CSV File"
#   - Choose: assets.csv
#   - Select PK: "asset_code"
#   - Click "Load to Database"
#   - See: "✅ Loaded 30 rows!"

# Step 2: Upload Document
#   - Click "Upload Document"
#   - Choose: Green-Line-Project.pdf
#   - Click "Process Document"
#   - See: "✅ Document processed! Chunks: 6"

# Step 3: Match
#   - Click "Run Matching"
#   - See: "✅ 4 matches" (13.3% match rate)

# 4. Explore Results
#   - View each asset's match details
#   - See which metadata fields matched
#   - Check page citations
#   - Export to JSON/CSV
```

---

## 🔍 Understanding Results

### Color Coding

| Color | Emoji | Confidence | Meaning |
|-------|-------|------------|---------|
| 🟢 Green | ✅ | ≥ 0.5 | High confidence match |
| 🟡 Yellow | ⚠️ | 0.3-0.5 | Medium confidence |
| 🔴 Red | 🔍 | < 0.3 | Low confidence |
| ⚫ Gray | ❌ | 0.0 | No match |

### Matched Fields Badges

Blue badges show which CSV columns matched:
- `[manufacturer=Schindler]`
- `[model=9300]`
- `[building=Building A]`

### Match Summary
- "Found on pages 12, 20" → Asset mentioned on these pages
- "No matches found" → Asset not in document

---

## 📊 Statistics Dashboard

### Charts

1. **Match Distribution**
   - Bar chart: Matched vs Not Matched

2. **Confidence Distribution**
   - Breakdown: High / Medium / Low confidence

3. **Top 10 Matches**
   - Ranked by confidence score

4. **Field Usage Analysis**
   - Which metadata fields match most often

---

## 💾 Export Options

### JSON Export
- Complete match results
- All metadata included
- Nested structure
- Perfect for processing

### CSV Export
- Flat table format
- Easy to import to Excel
- Good for reporting
- Columns: Asset Code, Name, Match Status, Confidence, Summary

---

## 🐛 Troubleshooting

### "Connection refused"

**Problem:** Can't connect to API

**Solution:**
```bash
curl http://localhost:8000/
# If fails:
docker compose up -d
```

### No matches showing

**Problem:** 0 rows matched

**Solutions:**
1. Lower confidence threshold to 0.10
2. Check assets were loaded (see sidebar status)
3. Verify document uploaded successfully
4. Check if asset codes in CSV match document content

### CSV not loading

**Problem:** Error when loading CSV

**Solutions:**
1. Check CSV format (valid headers, no extra commas)
2. Verify primary key column exists
3. Check for encoding issues (use UTF-8)

---

## 📁 Files

- **`complete_ui.py`** - Main application (600+ lines)
- **`run_ui.sh`** - Launch script with checks
- **`streamlit_requirements.txt`** - Dependencies

---

## 🎨 Customization

### Change API URL

Edit line 13 in `complete_ui.py`:
```python
API_URL = "http://your-api-url:8000"
```

### Modify CSV Table Name

Default is "assets". Change in the UI or edit line 174:
```python
value="your_table_name"
```

### Adjust Auto-Expand

Line 495 - change number of auto-expanded results:
```python
expanded=(idx < 5 and row['has_match'])  # Expand first 5
```

---

## ✅ Summary

**Complete 3-step workflow:**
1. 📊 Upload CSV → Database
2. 📄 Upload Document → Chunks
3. 🔍 Match → Results

**Rich visualization:**
- Color-coded matches
- Metadata field tracking
- Page citations
- Statistics dashboard
- Export capabilities

**Launch now:**
```bash
bash run_ui.sh
```

Enjoy the complete UI! 🎉
