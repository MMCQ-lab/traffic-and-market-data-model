#!/usr/bin/env bash
# Builds and validates this checkout using disposable resources only.
# Does not use Compose, .env, host ports, or any production volume/database.
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
test_id="$(date -u +%Y%m%d%H%M%S)-$$"
test_network="alt-data-test-$test_id"
test_db="alt-data-test-db-$test_id"
runner_image="alt-data-test:$test_id"
network_created=false
db_created=false
image_created=false

cleanup() {
  if "$db_created"; then docker rm -f "$test_db" >/dev/null || true; fi
  if "$network_created"; then docker network rm "$test_network" >/dev/null || true; fi
  if "$image_created"; then docker image rm "$runner_image" >/dev/null || true; fi
}
trap cleanup EXIT

docker build -t "$runner_image" .
image_created=true
docker run --rm --network none --entrypoint python "$runner_image" -m scripts.check_image
docker run --rm --network none -e POSTGRES_HOST=runtime-test --entrypoint python "$runner_image" \
  -c "from src.alt_data.config.settings import settings; assert settings.postgres_host == 'runtime-test'; print('Runtime override verified')"
docker network create --internal "$test_network" >/dev/null
network_created=true
docker run -d --rm --name "$test_db" --network "$test_network" \
  --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_DB=migration_test -e POSTGRES_USER=migration_test \
  -e POSTGRES_PASSWORD=migration_test postgres:16 >/dev/null
db_created=true

ready=false
for attempt in {1..45}; do
  if docker exec "$test_db" pg_isready -U migration_test -d migration_test >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
if ! "$ready"; then
  echo "Disposable PostgreSQL did not become ready" >&2
  exit 1
fi
docker run --rm --network "$test_network" -w /tmp \
  -e PYTHONPATH=/app -e ALT_DATA_ENV_FILE= -e REQUIRE_POSTGRES_TESTS=1 \
  -e "TEST_DATABASE_URL=postgresql+psycopg://migration_test:migration_test@$test_db:5432/migration_test" \
  --entrypoint python "$runner_image" -m pytest /app/tests -q -rs -p no:cacheprovider
echo "Isolated PostgreSQL suite and image checks passed"
