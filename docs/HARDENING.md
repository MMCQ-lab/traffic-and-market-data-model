# Reliability improvements — 2026-09-21

This work is prepared in the local checkout. Production has not been migrated,
rebuilt, or contacted. The previous review score has not been changed.

## Changes and evidence

| Area | Improvement | Test evidence |
| --- | --- | --- |
| Provider compliance | Database cooldown commits before network I/O; session advisory lock prevents overlap; failures retain the cooldown | `tests/test_request_gate.py`: denial without network, minimum interval, plus PostgreSQL overlap, failure, process-death and missing-schema cases |
| Ingestion transactions | Fetched payload commits separately; normalized writes and success status stay atomic | `tests/test_ingestion_lifecycle.py`: success/fetch/validation/write failure, SQLite and migrated PostgreSQL variants |
| Market integrity | Finite OHLCV, ordering, integer volume, UTC normalization, adjusted-close presence | `tests/test_market_validation.py` |
| Market scheduling | One symbol failing does not starve later symbols; overall exit remains nonzero | Batch failure test in `tests/test_market_validation.py` |
| Camera input | Empty/HTML/unrecognized feeds cannot count as successful ingestion | `tests/test_travel_midwest.py` |
| Database compatibility | New forward migrations `0006` and `0007`; historical `0001`–`0005` unchanged | Fresh schema/model parity, actual duplicate rejection, original upgrade/null failure cases, legacy camera-column preservation |
| Test isolation | Explicit external test URL is a provisioning connection; each test creates its own random database; never drops the supplied public schema | `tests/postgres_support.py`, `tests/test_test_database_safety.py` |
| CI | GitHub Actions runs the isolated Docker/PostgreSQL validator; any skip causes failure | `.github/workflows/tests.yml` |
| Image hygiene | Explicit Docker COPY list plus nested secret/cache exclusions; image filesystem inspection and synthetic runtime override | `scripts/check_image.py`, `deploy/validate-isolated.sh` |
| Configuration | URL components are escaped correctly; percent escapes survive Alembic config interpolation | `tests/test_settings.py`; `ALT_DATA_ENV_FILE=` disables dotenv |

Local verification: **35 passed, 16 skipped**. The 16 skips are PostgreSQL tests;
this Windows environment has neither Docker nor PostgreSQL. Bash syntax validation
and `git diff --check` passed. No PostgreSQL or rebuilt-image success is claimed for
this change yet. The workflow must run after these changes are pushed.

## Isolated validation

From an updated checkout on a Linux Docker host:

```bash
bash deploy/validate-isolated.sh
```

The script builds a uniquely named image without tagging the deployed ingestor,
uses a unique internal Docker network, gives PostgreSQL a disposable tmpfs data
directory, and publishes no database port. It never runs production Compose or
mounts the project/.env/production volume. Test credentials are disposable fixture
values. Cleanup removes only the image, container, and network it created.

Alternatively, `TEST_DATABASE_URL` may identify an isolated PostgreSQL server's
`migration_test` provisioning database. The test role must have CREATEDB. Tests
create/drop their own UUID-named databases and leave the provisioning database's
tables intact. Do not point this variable at a production server. In CI use
`REQUIRE_POSTGRES_TESTS=1` so missing infrastructure or skipped tests fail the run.

## Deployment gate (plan only)

1. Require a passing isolated run and inspect the migration diff. Do not deploy
   this client ahead of its schema: it intentionally refuses feed requests without
   `provider_request_gates`.
2. During an approved deployment window, pause ingestion timers and finish any
   active ingestion. Take and verify a backup. Record the actual deployed revision.
3. Review `0005`'s OHLCV NULL preflight for a server still at `0004`. Incompatible
   existing observations must be investigated; do not delete them to make upgrade pass.
4. Apply the forward chain only after approval. `0006` adds the gate table. `0007`
   adds a missing camera `created_at`, backfilled from `first_seen_at`; it leaves
   an already-existing column and its values unchanged.
5. Rebuild the official ingestor from the validated checkout, inspect image
   exclusions, and wait at least the configured provider interval after the last
   old-client request before the first new request. `0006` cannot reconstruct an
   attempt made by the old client before the gate existed.
6. Resume timers and inspect successful runs and freshness. All collectors for
   this provider must share the same PostgreSQL database and use the gate.

## Rollback and remaining limitations

- Prefer rolling back application code while retaining additive schema. `0007`'s
  downgrade deliberately retains the camera timestamp because it may predate the
  revision on production. Downgrading `0006` discards cooldown state; pause jobs
  and observe the provider interval when changing client versions.
- Advisory locks require a direct PostgreSQL connection or session pooling; do
  not place the gate behind transaction-pooling PgBouncer. Normal requests extend
  the cooldown from completion. A hard-killed process leaves its committed start
  claim. Cross-database collectors and restoring an older DB snapshot are outside
  this guarantee; coordinate them and wait the provider interval before resuming.
- Raw payload size remains bounded/truncated; Yahoo evidence is normalized JSON.
  This improves failure diagnosis but is not full deterministic replay.
- This does not solve historical publication times, intraday bar finality,
  revised market/GDP vintages, or query-level look-ahead prevention. Do not train
  a backtest by joining these tables on observation dates alone.
- Conflict-safe observation writes, alerting, dependency locks, and off-host
  backup/restore remain open. No new performance or portfolio-score claim is made.
