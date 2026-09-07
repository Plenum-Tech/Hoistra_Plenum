#!/bin/bash
# Launch the Complete Streamlit UI

echo "================================================"
echo "Starting RAG Platform Complete UI"
echo "================================================"
echo ""

# Check if streamlit is installed
if ! command -v streamlit &> /dev/null; then
    echo "Error: Streamlit not installed"
    echo ""
    echo "Install with:"
    echo "  pip install -r streamlit_requirements.txt"
    echo ""
    exit 1
fi

# Check if API is running
echo "Checking API connection..."
if curl -s http://localhost:8000/ > /dev/null 2>&1; then
    echo "✓ API is running at http://localhost:8000"
else
    echo "⚠ Warning: API not responding at http://localhost:8000"
    echo ""
    echo "Start the API with:"
    echo "  docker compose up -d"
    echo "  OR"
    echo "  uvicorn app.main:app --port 8000"
    echo ""
    echo "Continuing anyway..."
fi

echo ""
echo "Features:"
echo "  1. Upload CSV to database"
echo "  2. Upload PDF/DOCX/TXT for processing"
echo "  3. Match assets row-by-row"
echo "  4. View detailed results with field tracking"
echo ""
echo "Starting UI at http://localhost:8501"
echo "Press Ctrl+C to stop"
echo ""

# Launch streamlit
streamlit run complete_ui.py
