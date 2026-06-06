#!/usr/bin/env bash
set -e

API_URL="${API_BASE_URL:-https://etaxflow-api.onrender.com/api/v1}"

cat > frontend/public/taxflow/config.js <<EOF
window.TAXFLOW_API_BASE_URL = "${API_URL}";
EOF

echo "✓ config.js written → TAXFLOW_API_BASE_URL=${API_URL}"
