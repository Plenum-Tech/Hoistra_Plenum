# Streamlit UI for Document-Asset Matching

Beautiful web interface for uploading documents and viewing asset matching results.

## Features

✅ **Document Upload** - Drag & drop PDF, DOCX, or TXT files  
✅ **Row-by-Row Matching** - See which assets matched  
✅ **Metadata Field Tracking** - Know which fields matched  
✅ **Page Citations** - See where assets are mentioned  
✅ **Interactive Filters** - Sort, search, and filter results  
✅ **Statistics Dashboard** - Match quality analytics  
✅ **Export Results** - Download as JSON or CSV  

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r streamlit_requirements.txt
```

### 2. Start the API

Make sure your FastAPI backend is running:

```bash
docker compose up
# OR
uvicorn app.main:app --port 8000
```

### 3. Run Streamlit

```bash
streamlit run ui_app.py
```

The UI will open at: **http://localhost:8501**

---

## Usage

### Step 1: Upload Document

1. Click **"Upload"** tab
2. Choose a PDF, DOCX, or TXT file
3. Click **"🚀 Upload & Process"**
4. Wait for processing to complete

### Step 2: Run Matching

1. Click **"Match Results"** tab
2. Adjust confidence threshold if needed (sidebar)
3. Click **"🔍 Run Matching"**
4. View results!

### Step 3: Explore Results

Each asset shows:
- ✅ **Match status** (matched/not matched)
- 📊 **Confidence score** (0.0 to 1.0)
- 📄 **Document citations** (page numbers)
- 🏷️ **Matched metadata fields** (highlighted)
- 📝 **Chunk text** (preview or full)

### Step 4: View Statistics

Click **"Statistics"** tab to see:
- 📈 Match distribution charts
- 🏆 Top 10 best matches
- 🔍 Metadata field analysis
- 💾 Export options (JSON/CSV)

---

## Configuration

### Sidebar Settings

**Confidence Threshold** (0.0 - 1.0)
- Higher = stricter matching (fewer results)
- Lower = more permissive (more results)
- Default: 0.15

**Show Unmatched Assets**
- ✅ ON: Show all assets (matched and unmatched)
- ❌ OFF: Only show matched assets

**Show Full Chunk Text**
- ✅ ON: Display complete chunk text
- ❌ OFF: Show previews only (recommended)

### API URL

Edit `ui_app.py` line 21:

```python
API_URL = "http://localhost:8000"  # Change if API is elsewhere
```

---

## Screenshots

### Upload Tab
```
┌─────────────────────────────────────┐
│ 📤 Upload Document                   │
│                                     │
│ [Drag & drop file here]             │
│                                     │
│ File: contract.pdf ✓                │
│ [🚀 Upload & Process]               │
└─────────────────────────────────────┘
```

### Match Results Tab
```
┌─────────────────────────────────────┐
│ 🔍 Match Results                     │
│                                     │
│ ┌───┬───┬───┬───┐                  │
│ │ 30│ 15│ 15│75%│                  │
│ └───┴───┴───┴───┘                  │
│  Total Matched NotFound Rate        │
│                                     │
│ ✅ ESC-001 - Main Escalator (0.58)  │
│   📊 Asset Details | 📈 Summary     │
│   📄 Citations:                     │
│     Page 12: manufacturer=Schindler │
│               model=9300            │
│     "The Schindler 9300 escalator..." │
└─────────────────────────────────────┘
```

### Statistics Tab
```
┌─────────────────────────────────────┐
│ 📊 Match Statistics                  │
│                                     │
│ Match Distribution    Confidence    │
│ ┌────────┐           ┌────────┐    │
│ │ Matched│           │High    │    │
│ │████████│ 50%       │████    │ 8  │
│ │Not     │           │Medium  │    │
│ │████████│ 50%       │██      │ 4  │
│ └────────┘           └────────┘    │
│                                     │
│ 🏆 Top 10 Matches                   │
│ ┌────┬─────────┬──────────┐        │
│ │ 1  │ ESC-001 │ 0.580    │        │
│ │ 2  │ AHU-001 │ 0.520    │        │
│ └────┴─────────┴──────────┘        │
└─────────────────────────────────────┘
```

---

## Features in Detail

### 🎨 Color-Coded Results

- 🟢 **Green** - High confidence (≥0.5)
- 🟡 **Yellow** - Medium confidence (0.3-0.5)
- 🔴 **Red** - Low confidence (<0.3)
- ⚫ **Gray** - No match

### 🔍 Smart Filtering

**Sort by:**
- Confidence (high to low)
- Confidence (low to high)
- Asset code (alphabetical)
- Match count (most mentions first)

**Filter by:**
- All assets
- Matched only
- Unmatched only

**Search:**
- By asset code
- By asset name

### 📊 Statistics

**Charts:**
- Match distribution (pie/bar)
- Confidence distribution
- Metadata field frequency

**Tables:**
- Top matches ranking
- Field usage analysis

### 💾 Export

**JSON Export:**
- Complete match results
- All metadata included
- Ready for further processing

**CSV Export:**
- Flat table format
- Import into Excel
- Suitable for reporting

---

## Troubleshooting

### "Connection refused" error

**Problem:** Can't connect to API

**Solution:**
```bash
# Check API is running
curl http://localhost:8000/

# Start API if not running
docker compose up -d
```

### "No module named 'streamlit'" error

**Problem:** Streamlit not installed

**Solution:**
```bash
pip install -r streamlit_requirements.txt
```

### Empty results

**Problem:** No matches shown

**Solutions:**
1. Lower confidence threshold to 0.10
2. Check "Show Unmatched Assets"
3. Verify assets are loaded in database:
   ```bash
   python scripts/load_csv_to_postgres.py your_assets.csv
   ```

### Slow performance

**Problem:** UI is slow with many assets

**Solutions:**
1. Turn off "Show Full Chunk Text"
2. Use filters to reduce visible items
3. Increase API timeout in code

---

## Customization

### Change Theme

Edit `.streamlit/config.toml`:

```toml
[theme]
primaryColor = "#007bff"
backgroundColor = "#ffffff"
secondaryBackgroundColor = "#f0f2f6"
textColor = "#262730"
font = "sans serif"
```

### Add Custom Fields

Edit `ui_app.py` to display specific fields:

```python
# Line 230 - customize displayed fields
st.metric("Category", row["row_data"].get("category", "N/A"))
st.metric("Building", row["row_data"].get("building", "N/A"))
```

### Adjust Layout

```python
# Change column widths
col1, col2 = st.columns([3, 1])  # 3:1 ratio

# Change number of auto-expanded results
expanded=(idx < 5 and row['has_match'])  # Expand first 5
```

---

## Advanced Usage

### Run on Different Port

```bash
streamlit run ui_app.py --server.port 8502
```

### Run in Headless Mode

```bash
streamlit run ui_app.py --server.headless true
```

### Enable CORS

```bash
streamlit run ui_app.py --server.enableCORS false
```

---

## Example Workflow

```bash
# 1. Start API
docker compose up -d

# 2. Load your assets
python scripts/load_csv_to_postgres.py my_assets.csv

# 3. Start Streamlit
streamlit run ui_app.py

# 4. Open browser to http://localhost:8501

# 5. Upload document and view results!
```

---

## Support

For issues or questions:
- Check API logs: `docker compose logs -f`
- Verify database: `SELECT COUNT(*) FROM row_semantic_index;`
- Test API directly: `curl http://localhost:8000/`

---

## Summary

✨ **Beautiful UI** for document-asset matching  
🎯 **Interactive exploration** of results  
📊 **Rich analytics** and visualizations  
💾 **Export capabilities** for reporting  
⚙️ **Customizable** and extensible  

Enjoy your new UI! 🎉
