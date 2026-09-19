#!/usr/bin/env bash
# Read-only operational check for aliensserver. It does not call data providers.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

compose=(docker compose --env-file .env -f docker-compose.server.yml)

echo "== Docker service =="
systemctl is-active docker

echo
echo "== Scheduled jobs =="
systemctl is-active alternative-data-camera.timer
systemctl is-active alternative-data-market.timer
systemctl list-timers 'alternative-data-*' --no-pager

echo
echo "== Containers =="
"${compose[@]}" ps

echo
echo "== Persisted data =="
"${compose[@]}" exec -T db psql -U alternative_data -d alternative_data -c \
  "SELECT 'economic_indicators' AS dataset, COUNT(*) AS row_count FROM economic_indicators
   UNION ALL SELECT 'traffic_cameras', COUNT(*) FROM traffic_cameras
   UNION ALL SELECT 'market_prices', COUNT(*) FROM market_prices
   UNION ALL SELECT 'ingestion_runs', COUNT(*) FROM ingestion_runs
   ORDER BY dataset;"

echo
echo "== Most recent ingestion runs =="
"${compose[@]}" exec -T db psql -U alternative_data -d alternative_data -c \
  "SELECT id, status, rows_received, rows_inserted, rows_skipped, started_at
   FROM ingestion_runs ORDER BY id DESC LIMIT 10;"
