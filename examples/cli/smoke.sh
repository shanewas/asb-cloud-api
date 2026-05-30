#!/usr/bin/env bash
# Smoke test script for asb CLI against local self-hosted API.
set -euo pipefail

: "${ASB_API_KEY:?Set ASB_API_KEY from API startup logs}"
BASE_URL="${ASB_BASE_URL:-http://localhost:8000}"

echo "== Health =="
asb --base-url "$BASE_URL" health --pretty

echo -e "\n== Scrape =="
asb --base-url "$BASE_URL" scrape --url https://example.com --method GET --timeout 20 --pretty || echo "scrape may need real browser; check output above"

echo -e "\n== Usage =="
asb --base-url "$BASE_URL" usage --pretty || true

echo -e "\nCLI smoke script finished."
