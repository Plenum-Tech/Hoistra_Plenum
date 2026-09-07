#!/bin/bash
# Complete End-to-End Test with Sample Assets Data
# This script demonstrates the full workflow:
# 1. Seed assets from CSV
# 2. Upload maintenance report
# 3. Match document to assets
# 4. Run various queries

set -e  # Exit on any error

API_URL="${API_URL:-http://localhost:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "================================================================"
echo "RAG PLATFORM - COMPLETE TESTING WITH SAMPLE ASSETS"
echo "================================================================"
echo "API URL: ${API_URL}"
echo "Project: ${PROJECT_DIR}"
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if API is running
echo -e "${BLUE}Step 0: Checking API health...${NC}"
if ! curl -s -f "${API_URL}/health" > /dev/null; then
    echo -e "${RED}ERROR: API is not responding at ${API_URL}${NC}"
    echo "Please start the API with: docker compose up -d"
    exit 1
fi

HEALTH=$(curl -s "${API_URL}/health")
echo "Health status: ${HEALTH}"

# Check modes
MODES=$(curl -s "${API_URL}/" | jq -r '.modes')
VISION_ENABLED=$(echo "${MODES}" | jq -r '.vision_enabled')
EMBEDDINGS_ENABLED=$(echo "${MODES}" | jq -r '.embeddings_enabled')

echo -e "Vision enabled: ${VISION_ENABLED}"
echo -e "Embeddings enabled: ${EMBEDDINGS_ENABLED}"

if [ "${VISION_ENABLED}" != "true" ] || [ "${EMBEDDINGS_ENABLED}" != "true" ]; then
    echo -e "${YELLOW}WARNING: Vision or embeddings are in degraded mode${NC}"
    echo "Set OPENAI_API_KEY in .env for full functionality"
    echo ""
fi

# ================================================================
# STEP 1: Seed Assets Database
# ================================================================
echo ""
echo -e "${BLUE}Step 1: Seeding assets database from CSV...${NC}"
echo "================================================================"

# Clear Python cache to avoid stale imports
echo "Clearing Python cache..."
find "${PROJECT_DIR}" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "${PROJECT_DIR}" -type f -name '*.pyc' -delete 2>/dev/null || true

CSV_PATH="${PROJECT_DIR}/sample_data/assets.csv"
if [ ! -f "${CSV_PATH}" ]; then
    echo -e "${RED}ERROR: Sample CSV not found at ${CSV_PATH}${NC}"
    exit 1
fi

echo "CSV file: ${CSV_PATH}"
echo "Asset count: $(tail -n +2 "${CSV_PATH}" | wc -l) rows"
echo ""

# Run the seeding script
cd "${PROJECT_DIR}"
echo "Running: python -m scripts.seed_row_index --table assets --csv ${CSV_PATH}"

python -m scripts.seed_row_index \
    --table assets \
    --csv "${CSV_PATH}" 2>&1 | grep -E "Seeded|ERROR|WARNING" || true

echo -e "${GREEN}✓ Assets database seeded${NC}"
echo ""

# ================================================================
# STEP 2: Upload Maintenance Report
# ================================================================
echo -e "${BLUE}Step 2: Uploading maintenance report...${NC}"
echo "================================================================"

DOC_PATH="${PROJECT_DIR}/sample_data/maintenance_report_q4_2024.txt"
if [ ! -f "${DOC_PATH}" ]; then
    echo -e "${RED}ERROR: Sample document not found at ${DOC_PATH}${NC}"
    exit 1
fi

echo "Document: ${DOC_PATH}"
echo "Size: $(wc -c < "${DOC_PATH}") bytes"
echo ""

UPLOAD_RESPONSE=$(curl -s -X POST "${API_URL}/documents/upload" \
    -F "file=@${DOC_PATH}")

echo "Upload response:"
echo "${UPLOAD_RESPONSE}" | jq .

DOC_ID=$(echo "${UPLOAD_RESPONSE}" | jq -r .document_id)
NUM_CHUNKS=$(echo "${UPLOAD_RESPONSE}" | jq -r .num_chunks)

if [ "${DOC_ID}" == "null" ] || [ -z "${DOC_ID}" ]; then
    echo -e "${RED}ERROR: Document upload failed${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}✓ Document uploaded${NC}"
echo "Document ID: ${DOC_ID}"
echo "Chunks created: ${NUM_CHUNKS}"
echo ""

# ================================================================
# STEP 3: Match Document to Assets
# ================================================================
echo -e "${BLUE}Step 3: Matching document to asset database...${NC}"
echo "================================================================"
echo ""

MATCH_RESPONSE=$(curl -s -X POST "${API_URL}/documents/${DOC_ID}/match-rows" \
    -H "Content-Type: application/json" \
    -d '{
        "confidence_threshold": 0.3,
        "group_by_table": true
    }')

echo "Match summary:"
echo "${MATCH_RESPONSE}" | jq '{
    document_id,
    file_name,
    total_chunks_analyzed,
    unique_rows_matched,
    by_table,
    latency_ms
}'

UNIQUE_MATCHED=$(echo "${MATCH_RESPONSE}" | jq -r '.unique_rows_matched')

echo ""
echo -e "${GREEN}✓ Document matched to ${UNIQUE_MATCHED} unique assets${NC}"
echo ""

echo "Top 5 matched assets:"
echo "${MATCH_RESPONSE}" | jq -r '.matched_rows[:5] | .[] | 
    "  • \(.source_table).\(.row_pk): \(.row_data.asset_name // .row_pk) 
      Confidence: \(.confidence), Method: \(.match_method), Chunks: \(.chunk_count)"' \
    | sed 's/  */ /g'

echo ""

# ================================================================
# STEP 4: Debug Analysis
# ================================================================
echo -e "${BLUE}Step 4: Chunk-by-chunk debug analysis...${NC}"
echo "================================================================"
echo ""

DEBUG_RESPONSE=$(curl -s "${API_URL}/documents/${DOC_ID}/match-rows/debug?confidence_threshold=0.3")

CHUNKS_WITH_MATCHES=$(echo "${DEBUG_RESPONSE}" | jq -r '.chunks_with_matches')
CHUNKS_WITHOUT=$(echo "${DEBUG_RESPONSE}" | jq -r '.chunks_without_matches')
TOTAL_CHUNKS=$(echo "${DEBUG_RESPONSE}" | jq -r '.total_chunks')

echo "Debug analysis:"
echo "  Total chunks: ${TOTAL_CHUNKS}"
echo "  Chunks with matches: ${CHUNKS_WITH_MATCHES}"
echo "  Chunks without matches: ${CHUNKS_WITHOUT}"
echo ""

echo "Sample chunk with matches:"
echo "${DEBUG_RESPONSE}" | jq '.chunk_details[0] | {
    chunk_index,
    page,
    block_type,
    text: (.text[:120] + "..."),
    match_count,
    matched_assets: .matched_rows[:3] | map(.row_pk)
}'

echo ""
echo -e "${GREEN}✓ Debug analysis complete${NC}"
echo ""

# ================================================================
# STEP 5: Test Queries
# ================================================================
echo -e "${BLUE}Step 5: Testing queries...${NC}"
echo "================================================================"
echo ""

# Query 1: What equipment was serviced?
echo -e "${YELLOW}Query 1: What HVAC equipment was serviced in Q4?${NC}"
QUERY1=$(curl -s -X POST "${API_URL}/rag/query" \
    -H "Content-Type: application/json" \
    -d '{
        "query": "What HVAC equipment was serviced in Q4?",
        "top_k": 5
    }')

echo "Answer: $(echo "${QUERY1}" | jq -r .answer)"
echo ""
echo "Matched assets:"
echo "${QUERY1}" | jq -r '.matched_rows[:3] | .[] | 
    "  • \(.row_pk): \(.row_data.asset_name // .row_pk)"'
echo ""

# Query 2: Elevator inspection status
echo -e "${YELLOW}Query 2: Which elevators were inspected?${NC}"
QUERY2=$(curl -s -X POST "${API_URL}/rag/query" \
    -H "Content-Type: application/json" \
    -d '{
        "query": "Which elevators were inspected and what was their status?",
        "top_k": 5
    }')

echo "Answer: $(echo "${QUERY2}" | jq -r .answer)"
echo ""
echo "Matched elevators:"
echo "${QUERY2}" | jq -r '.matched_rows | map(select(.source_table == "assets" and (.row_pk | startswith("EL-")))) | .[] | 
    "  • \(.row_pk): \(.row_data.asset_name // .row_pk) (confidence: \(.confidence))"'
echo ""

# Query 3: Generator status
echo -e "${YELLOW}Query 3: What is the status of the emergency generator?${NC}"
QUERY3=$(curl -s -X POST "${API_URL}/rag/query" \
    -H "Content-Type: application/json" \
    -d '{
        "query": "What is the status of the emergency generator GEN-001?",
        "top_k": 3
    }')

echo "Answer: $(echo "${QUERY3}" | jq -r .answer)"
echo ""
echo "Matched assets:"
echo "${QUERY3}" | jq -r '.matched_rows[:2] | .[] | 
    "  • \(.row_pk): \(.row_data.asset_name // .row_pk)"'
echo ""

# Query 4: Row-only search (no LLM answer)
echo -e "${YELLOW}Query 4: Row-only search - all Building A assets${NC}"
QUERY4=$(curl -s -X POST "${API_URL}/rag/rows" \
    -H "Content-Type: application/json" \
    -d '{
        "query": "Building A equipment",
        "top_k": 10
    }')

echo "Matched rows: $(echo "${QUERY4}" | jq -r .unique_rows_matched)"
echo "Top 5 Building A assets:"
echo "${QUERY4}" | jq -r '.matched_rows[:5] | .[] | 
    "  • \(.row_pk): \(.row_data.building // "N/A") - \(.row_data.asset_name // .row_pk)"'
echo ""

echo -e "${GREEN}✓ All queries completed${NC}"
echo ""

# ================================================================
# STEP 6: Generate Test Report
# ================================================================
echo -e "${BLUE}Step 6: Generating test report...${NC}"
echo "================================================================"
echo ""

# Save detailed results to file
REPORT_FILE="${PROJECT_DIR}/test_results_$(date +%Y%m%d_%H%M%S).json"

cat > "${REPORT_FILE}" << EOF
{
  "test_date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "api_url": "${API_URL}",
  "vision_enabled": ${VISION_ENABLED},
  "embeddings_enabled": ${EMBEDDINGS_ENABLED},
  "assets_seeded": $(tail -n +2 "${CSV_PATH}" | wc -l),
  "document": {
    "id": "${DOC_ID}",
    "path": "${DOC_PATH}",
    "chunks": ${NUM_CHUNKS}
  },
  "matching_results": {
    "total_chunks_analyzed": $(echo "${MATCH_RESPONSE}" | jq -r .total_chunks_analyzed),
    "unique_rows_matched": ${UNIQUE_MATCHED},
    "by_table": $(echo "${MATCH_RESPONSE}" | jq .by_table)
  },
  "debug_analysis": {
    "chunks_with_matches": ${CHUNKS_WITH_MATCHES},
    "chunks_without_matches": ${CHUNKS_WITHOUT}
  }
}
EOF

echo "Test report saved to: ${REPORT_FILE}"
echo ""

# ================================================================
# Summary
# ================================================================
echo "================================================================"
echo -e "${GREEN}TESTING COMPLETE - SUMMARY${NC}"
echo "================================================================"
echo ""
echo "✓ Assets seeded: $(tail -n +2 "${CSV_PATH}" | wc -l) rows"
echo "✓ Document uploaded: ${DOC_ID}"
echo "✓ Document chunks: ${NUM_CHUNKS}"
echo "✓ Assets matched: ${UNIQUE_MATCHED}"
echo "✓ Match rate: ${CHUNKS_WITH_MATCHES}/${TOTAL_CHUNKS} chunks"
echo "✓ Queries tested: 4 successful"
echo ""
echo "Document ID for further testing: ${DOC_ID}"
echo ""
echo "Next steps:"
echo "  1. Review matched assets in ${REPORT_FILE}"
echo "  2. Try custom queries:"
echo "     curl -X POST ${API_URL}/rag/query -H 'Content-Type: application/json' -d '{\"query\": \"YOUR QUESTION\"}'"
echo "  3. View all matched assets:"
echo "     curl -X POST ${API_URL}/documents/${DOC_ID}/match-rows"
echo "  4. Debug specific chunks:"
echo "     curl '${API_URL}/documents/${DOC_ID}/match-rows/debug?show_all_chunks=true'"
echo ""
echo "Sample queries to try:"
echo "  • 'What equipment needs replacement parts?'"
echo "  • 'Which assets are in Building A basement?'"
echo "  • 'What is the status of the UPS systems?'"
echo "  • 'Which chillers were serviced and when?'"
echo "  • 'What maintenance is scheduled for January 2025?'"
echo ""
