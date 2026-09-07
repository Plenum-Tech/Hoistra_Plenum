#!/usr/bin/env bash
# Vendor Swagger UI + ReDoc assets into ./static/ so /docs and /redoc
# work without internet / CDN access at runtime.
#
# Tries jsdelivr first, falls back to unpkg if that's blocked. Both serve
# the same npm packages.
#
# Usage:  bash scripts/fetch_docs_assets.sh

set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p static

SWAGGER_VER="5.17.14"
REDOC_VER="2.1.5"

fetch() {
    local out="$1"
    local path="$2"
    for base in \
        "https://cdn.jsdelivr.net/npm" \
        "https://unpkg.com" \
        ; do
        url="${base}/${path}"
        echo "  trying ${url}"
        if curl -fsSL --max-time 30 -o "${out}" "${url}"; then
            local size
            size=$(stat -c%s "${out}" 2>/dev/null || stat -f%z "${out}")
            echo "  ✓ saved ${out} (${size} bytes)"
            return 0
        fi
    done
    echo "  ✗ all CDNs failed for ${path}" >&2
    return 1
}

echo "Fetching Swagger UI ${SWAGGER_VER}..."
fetch static/swagger-ui-bundle.js  "swagger-ui-dist@${SWAGGER_VER}/swagger-ui-bundle.js"
fetch static/swagger-ui.css        "swagger-ui-dist@${SWAGGER_VER}/swagger-ui.css"

echo "Fetching ReDoc ${REDOC_VER}..."
fetch static/redoc.standalone.js   "redoc@${REDOC_VER}/bundles/redoc.standalone.js"

echo
echo "Done. /docs and /redoc will now load assets from ./static/"
ls -lh static/
