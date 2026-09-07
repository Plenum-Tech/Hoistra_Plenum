# UI Quick Start Guide

Launch the web interface in 3 steps!

## Option 1: One Command

```bash
bash run_ui.sh
```

## Option 2: Manual Steps

```bash
# 1. Install dependencies (one time only)
pip install -r streamlit_requirements.txt

# 2. Make sure API is running
docker compose up -d

# 3. Launch UI
streamlit run ui_app.py
```

## Access

Open your browser to: **http://localhost:8501**

## File Overview

- `ui_app.py` - Main Streamlit application
- `run_ui.sh` - Launch script (checks API, starts UI)
- `streamlit_requirements.txt` - Python dependencies
- `STREAMLIT_UI_GUIDE.md` - Detailed documentation

## What If API is Not Running?

**Error:** "Connection refused" or API not responding

**Solution:**
```bash
# Start the API
docker compose up -d

# Verify it's running
curl http://localhost:8000/
```

## What If Streamlit is Not Installed?

**Error:** "streamlit: command not found"

**Solution:**
```bash
pip install -r streamlit_requirements.txt
```

## Usage

1. **Upload** a document (PDF, DOCX, TXT)
2. **Click** "Upload & Process"
3. **Switch** to "Match Results" tab
4. **Click** "Run Matching"
5. **Explore** results!

## Features

✅ Upload documents via drag & drop  
✅ See all assets row-by-row  
✅ View which metadata fields matched  
✅ See page citations  
✅ Filter, sort, search results  
✅ Export to JSON or CSV  
✅ View statistics and charts  

Enjoy! 🎉
