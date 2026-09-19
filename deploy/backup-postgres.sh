#!/usr/bin/env bash
# Create a local, timestamped PostgreSQL backup on the server.
# This script never restores or deletes database data.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="${BACKUP_DIR:-$project_dir/backups}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_file="$backup_dir/alternative_data_$timestamp.dump"
temporary_file="$backup_file.partial"

cd "$project_dir"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"

compose=(docker compose --env-file .env -f docker-compose.server.yml)

echo "Creating PostgreSQL backup at $backup_file"
"${compose[@]}" exec -T db pg_dump \
  -U "${POSTGRES_USER:-alternative_data}" \
  -d "${POSTGRES_DB:-alternative_data}" \
  --format=custom > "$temporary_file"

chmod 600 "$temporary_file"
mv "$temporary_file" "$backup_file"
echo "Backup complete: $backup_file"
