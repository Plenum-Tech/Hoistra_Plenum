#!/bin/bash
# Fix embedding service issue by clearing Python cache

echo "================================================================"
echo "FIXING EMBEDDING SERVICE ISSUE"
echo "================================================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Project directory: ${PROJECT_DIR}"
echo ""

# Step 1: Clear Python cache
echo "Step 1: Clearing Python cache..."
cd "${PROJECT_DIR}"

PYCACHE_COUNT=$(find . -type d -name '__pycache__' 2>/dev/null | wc -l)
PYC_COUNT=$(find . -type f -name '*.pyc' 2>/dev/null | wc -l)

echo "  Found ${PYCACHE_COUNT} __pycache__ directories"
echo "  Found ${PYC_COUNT} .pyc files"

find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find . -type f -name '*.pyc' -delete 2>/dev/null || true

echo "  ✓ Cache cleared"
echo ""

# Step 2: Test embedding service
echo "Step 2: Testing embedding service..."
python -m scripts.diagnose_embedding

if [ $? -eq 0 ]; then
    echo ""
    echo "================================================================"
    echo "✓ FIXED - Embedding service is now working"
    echo "================================================================"
    echo ""
    echo "You can now run:"
    echo "  python -m scripts.seed_row_index --table assets --csv sample_data/assets.csv"
    echo "  bash scripts/test_with_sample_data.sh"
else
    echo ""
    echo "================================================================"
    echo "✗ ISSUE PERSISTS"
    echo "================================================================"
    echo ""
    echo "If you're in Docker, try:"
    echo "  docker compose down"
    echo "  docker compose up --build"
    echo ""
    echo "If running locally, try:"
    echo "  deactivate  # exit virtualenv"
    echo "  rm -rf .venv"
    echo "  python -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
fi
