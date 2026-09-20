# Quant DevOps / Production Engineering Portfolio Audit

**Audit date:** 2026-09-19  
**Scope:** Read-only review of the checked-out repository. No application code, deployment configuration, database, credentials, `.env`, or private keys were changed or read.  
**Test evidence:** `python -m pytest -q -p no:cacheprovider` completed successfully: **9 passed in 0.99s**.

## Executive verdict

**OVERALL SCORE: 5.6 / 10**  
**STATUS: NOT ACCEPTABLE**

The repository is a credible early-career ingestion prototype with real operational work behind it: PostgreSQL, a base ingestion lifecycle, migrations, provider adapters, systemd timers, a private production database binding, a health check, and a backup helper. Those are meaningful portfolio strengths.

It is not yet acceptable for a top quantitative-trading production-infrastructure portfolio claim because the two central claims—point-in-time correctness and production reliability—are not actually guaranteed by the implementation. Historical economic revisions are discarded, daily market bars have no defensible availability timestamp, provider rate limiting is only in process memory, migrations depend on current ORM metadata, backup is not recoverable by demonstrated procedure, and tests do not exercise PostgreSQL, migrations, the base lifecycle, or deployment behavior.

No P0 issue was found. P1 items below are substantial enough that a reviewer would expect them to be fixed before describing this as research-ready or production-ready.

## Scope and verified strengths

- `.env` is ignored by Git; no tracked credential or private-key file was found.
- Provider parsing is separated from normalized persistence: `WorldBankGdpIngestor`, `YahooFinanceMarketIngestor`, and `TravelMidwestCameraIngestor` extend `BaseIngestor`.
- The schema has useful uniqueness constraints for economic observations, market prices, traffic cameras, and several transportation entities.
- The production Compose file binds PostgreSQL only to `127.0.0.1` and supplies a database health check.
- Ingestion runs are committed before network work, so a raised fetch error can be recorded as a failed run.
- Camera parsing has fixture coverage for XML, CSV, the deprecated `ImgPath` behavior, and single-session duplicate handling. Market and GDP tests cover simple normalization and repeat-save behavior.
- The server scripts provide a read-only health check and a PostgreSQL custom-format dump helper.

These are good foundations. The findings below focus on what the implementation does **not** yet prove.

## Findings

### P1 — Point-in-time availability is not modeled well enough to prevent market-data leakage

**Evidence**

- [src/alt_data/ingestion/market/yahoo_finance.py](../src/alt_data/ingestion/market/yahoo_finance.py#L45) turns a Yahoo daily-bar timestamp into `observed_at`.
- [src/alt_data/ingestion/market/yahoo_finance.py](../src/alt_data/ingestion/market/yahoo_finance.py#L130) always writes `published_at=None`.
- [src/alt_data/models/all_models.py](../src/alt_data/models/all_models.py) makes `observed_at`, `published_at`, and `retrieved_at` available in the model, but no availability rule, query helper, or view enforces their use.
- [README.md](../README.md#L13) says the fields support look-ahead-bias controls, while [docs/PROJECT_NOTES.md](PROJECT_NOTES.md#L56) says future joins must use availability time. Neither is implemented.

**Why it matters**

A daily OHLCV bar is known only after a source publishes a completed session. A timestamp associated with the bar is not automatically its availability time. Backfilling the whole history today also gives every row a modern `retrieved_at`, which is correct provenance but cannot reconstruct what an earlier strategy could have known. A future date join can silently use a same-day close before it was available.

**Smallest reasonable correction**

Define and document one explicit daily-bar availability convention (for example, official exchange close plus a conservative source-publication delay). Store `source_published_at` or `available_at` separately from the session date and build one point-in-time query/view that filters `available_at <= decision_time`. Add leakage tests around market close, weekends, and historical backfills.

### P1 — Economic revisions are permanently discarded, so historical GDP is not point-in-time reproducible

**Evidence**

- [src/alt_data/ingestion/economic/world_bank.py](../src/alt_data/ingestion/economic/world_bank.py#L47) treats an existing `(source, indicator, geography, observed_at)` record as a duplicate and skips it.
- [src/alt_data/models/all_models.py](../src/alt_data/models/all_models.py) defines `uq_economic_observation` on exactly those fields.
- [src/alt_data/ingestion/economic/world_bank.py](../src/alt_data/ingestion/economic/world_bank.py#L56) sets `published_at=None`.

**Why it matters**

Macro data is revised. The implementation retains only the first value fetched for a period, not the value released at a particular time and not the latest corrected value. That makes both revision-aware research and later data-quality correction impossible, despite the project goal of point-in-time research.

**Smallest reasonable correction**

Model observations as vintages: retain source publication/release time, value, and retrieval time, and make the idempotency key include an immutable source version or content hash. Provide a separate latest-vintage view instead of overwriting or skipping revisions.

### P1 — Alembic migrations are not immutable database definitions

**Evidence**

- [alembic/versions/0001_initial_schema.py](../alembic/versions/0001_initial_schema.py#L4) imports current `Base.metadata`, and [line 12](../alembic/versions/0001_initial_schema.py#L12) calls `create_all` using the current model definitions.
- [alembic/versions/0002_transportation_schema.py](../alembic/versions/0002_transportation_schema.py#L4) does the same for Phase 2 tables.
- [alembic/versions/0004_market_price_fields.py](../alembic/versions/0004_market_price_fields.py#L14) adds missing market columns as `nullable=True`, while the current ORM model declares the fields non-nullable.

**Why it matters**

Applying migration `0001` today does not necessarily create the same schema it created when first authored; it creates whichever current tables the ORM exposes. Two databases with different migration histories can therefore have different nullability and constraints while reporting the same Alembic revision. This breaks reproducible deployment and makes schema rollback/review unreliable.

**Smallest reasonable correction**

Replace metadata-driven historical migrations with explicit Alembic DDL captured at the time of each change. Add a migration that backfills/validates existing market data and then enforces `NOT NULL` for required OHLCV fields. Test upgrade from an empty PostgreSQL database and from each supported prior revision.

**Implementation evidence — 2026-09-19**

- `0001_initial_schema` and `0002_transportation_schema` now use explicit `op.create_table` / `op.drop_table` operations and no longer import `Base` or application model modules.
- `0005_market_price_not_null` is a forward-only reconciliation migration. It verifies `market_prices` and all five required OHLCV columns exist, counts incompatible NULL rows before DDL, aborts on incompatibility, and otherwise changes only `open`, `high`, `low`, `adjusted_close`, and `volume` to `NOT NULL` in place.
- `tests/test_migrations_postgres.py` adds disposable-PostgreSQL tests for zero-to-head migration, `0004`-to-`0005` data preservation, safe NULL-data failure, final nullability/unique constraint checks, a future-metadata probe, and static guards against ORM-metadata imports in historical migrations.
- Local non-Docker validation completed: migration/static tests passed as part of `11 passed, 4 skipped`. The four PostgreSQL integration tests were skipped because the local Windows workspace has no available Docker CLI/daemon. The audit score remains unchanged until those tests run successfully on a Docker-capable non-production machine.

### P1 — Travel Midwest rate-limit compliance is not durable across processes or reboots

**Evidence**

- [src/alt_data/ingestion/traffic/travel_midwest.py](../src/alt_data/ingestion/traffic/travel_midwest.py#L23) keeps the last request only in a class-level Python dictionary using `time.monotonic()`.
- Every systemd service invocation starts a new Docker process: [deploy/systemd/alternative-data-camera.service](../deploy/systemd/alternative-data-camera.service#L8).
- The timer triggers two minutes after every boot: [deploy/systemd/alternative-data-camera.timer](../deploy/systemd/alternative-data-camera.timer#L5).
- The project documents a no-more-than-five-minute provider rule: [README.md](../README.md#L58).

**Why it matters**

The in-memory guard disappears at the end of each job, on container restart, and on server reboot. A manual run followed by reboot or another job can request the same feed within five minutes. The hourly schedule is normally conservative, but the code does not enforce the policy across the real deployment boundary.

**Smallest reasonable correction**

Persist a per-provider/per-endpoint last-attempt timestamp transactionally in PostgreSQL and refuse a new request until the interval has elapsed. Make the timer consult that durable gate, including at boot. Add a test covering two separate process instances and a simulated reboot.

### P1 — Backup exists, but recovery has not been made operationally credible

**Evidence**

- [deploy/backup-postgres.sh](../deploy/backup-postgres.sh#L11) writes a dump to a local `backups/` directory on the same server.
- [deploy/README.md](../deploy/README.md) documents backup creation but deliberately provides no tested restore procedure.
- There is no backup timer, retention policy, checksum verification, encryption, off-host copy, restore test, or test coverage for the script.

**Why it matters**

A local manual dump does not protect against host loss, disk corruption, operator deletion, or an untested restore. For production-engineering review, “a backup command exists” is weaker than “a restore has been exercised and recovery time is known.”

**Smallest reasonable correction**

Document a guarded restore runbook, add an automated scheduled backup with bounded retention, copy encrypted backups to separate storage, and regularly restore a dump into an isolated database to verify it. Record the last successful backup and restore-test timestamps in monitoring.

### P1 — The test suite does not test the production database or the ingestion lifecycle

**Evidence**

- All persistence tests use SQLite in memory, for example [tests/test_yahoo_finance.py](../tests/test_yahoo_finance.py#L39).
- Tests call `Base.metadata.create_all`, not Alembic migrations, for example [tests/test_yahoo_finance.py](../tests/test_yahoo_finance.py#L40).
- There is no test for `BaseIngestor.run()` success/failure paths, raw-payload persistence, failed-run commit, concurrent writes, PostgreSQL constraint behavior, Docker Compose, systemd, backup creation, or restore.

**Why it matters**

SQLite differs materially from PostgreSQL in types, constraints, transaction behavior, and DDL. The current nine tests prove small parser and single-session save examples, not that the deployed schema or failure behavior works. The passing suite creates a false sense of operational coverage.

**Smallest reasonable correction**

Run integration tests against disposable PostgreSQL in CI, execute `alembic upgrade head`, and test successful/failed `BaseIngestor.run()` transactions. Add fixture-driven provider-client tests, concurrency/idempotency tests, Compose smoke tests, and a backup/restore integration test.

### P2 — Idempotency uses check-then-insert and is not safe for concurrent jobs

**Evidence**

- Market persistence does a `SELECT` followed by `add`: [src/alt_data/ingestion/market/yahoo_finance.py](../src/alt_data/ingestion/market/yahoo_finance.py#L121).
- GDP does the same: [src/alt_data/ingestion/economic/world_bank.py](../src/alt_data/ingestion/economic/world_bank.py#L47).
- Camera persistence does the same: [src/alt_data/ingestion/traffic/cameras.py](../src/alt_data/ingestion/traffic/cameras.py) in `save`.
- The database has unique constraints, but the code does not use PostgreSQL `ON CONFLICT` or recover an `IntegrityError` for an expected concurrent duplicate.

**Why it matters**

Two overlapping jobs can both see no existing row. One commit then causes the other whole transaction to fail. This is also an N+1-query pattern that becomes slow as history or camera counts grow.

**Smallest reasonable correction**

Use PostgreSQL bulk inserts with `ON CONFLICT DO NOTHING` or carefully scoped upserts, and derive inserted/skipped counts from their results. Add a concurrent-run integration test and an index/plan check for expected query volume.

### P2 — Successful camera jobs overwrite mutable metadata without preserving its history

**Evidence**

- Existing rows update `last_seen_at`, URLs, age flags, and active status in [src/alt_data/ingestion/traffic/cameras.py](../src/alt_data/ingestion/traffic/cameras.py) `save`.
- There is no insert into `camera_snapshots` or an observation/version table during camera metadata ingestion.
- When the CSV lacks an ID, identity is derived from location, coordinates, and direction in [src/alt_data/ingestion/traffic/travel_midwest.py](../src/alt_data/ingestion/traffic/travel_midwest.py#L109).

**Why it matters**

The table becomes a latest-state catalog, not a time series. A changed URL/location/direction can overwrite prior state; a changed identity component can create a new synthetic camera. This is insufficient to support later historical transportation analysis or to demonstrate stable entity resolution.

**Smallest reasonable correction**

Treat `traffic_cameras` as an entity table and create a versioned metadata-observation table keyed by camera and retrieval time. Define and document a stable identity strategy for the provider’s ID-less CSV, with tests for field changes and collisions.

### P2 — Provider provenance is incomplete and Yahoo raw payload metadata is synthetic

**Evidence**

- Yahoo fetches `query1.finance.yahoo.com/v8/finance/chart/...` at [src/alt_data/ingestion/market/yahoo_finance.py](../src/alt_data/ingestion/market/yahoo_finance.py#L22).
- It then stores a transformed JSON list and constructs a different `finance.yahoo.com/quote/.../history` URL at [lines 96-98](../src/alt_data/ingestion/market/yahoo_finance.py#L96).
- `BaseIngestor` only creates `RawPayload` after `fetch()` succeeds at [src/alt_data/ingestion/base.py](../src/alt_data/ingestion/base.py#L50); failed HTTP response metadata/body is not retained.

**Why it matters**

The audit table cannot reproduce the source response or prove which endpoint/parameters produced a row. For data debugging and a research trail, transformed payloads labeled as raw are misleading and failures lose useful provider evidence.

**Smallest reasonable correction**

Have provider clients return the actual canonical request URL, selected headers/status, and raw response bytes; store a content hash and bounded raw body. Record response metadata for non-success HTTP responses while redacting credentials and sensitive headers.

### P2 — Scheduled jobs have weak failure isolation, alerting, and ownership controls

**Evidence**

- One symbol exception stops the remaining market universe: [scripts/ingest_yahoo_finance.py](../scripts/ingest_yahoo_finance.py#L12).
- The systemd services have no explicit `User=`, `Group=`, resource limits, timeout, retry policy, failure hook, or alert target: [deploy/systemd/alternative-data-market.service](../deploy/systemd/alternative-data-market.service) and [deploy/systemd/alternative-data-camera.service](../deploy/systemd/alternative-data-camera.service).
- Observability is a manual health script and journal inspection: [deploy/check-server-health.sh](../deploy/check-server-health.sh).

**Why it matters**

One bad ticker can hide updates for all later tickers. A systemd unit without an explicit non-root service identity runs as root by default. Failures are visible only when somebody manually checks, and there is no SLO, metric, alert, or retry/backoff policy tailored per provider.

**Smallest reasonable correction**

Make each symbol independently reported while preserving a non-zero aggregate failure status. Run units under a dedicated least-privilege user with a controlled Docker access strategy, explicit timeouts and hardening directives. Export run freshness/failure metrics and send a failure notification; use provider-specific retry/backoff only where policy permits.

### P2 — Security differs sharply between development and production Compose files

**Evidence**

- Development Compose publishes PostgreSQL on all interfaces and has a fallback password: [docker-compose.yml](../docker-compose.yml#L7) and [docker-compose.yml](../docker-compose.yml#L10).
- `Settings` also defaults to `postgres_password = "change_me"`: [src/alt_data/config/settings.py](../src/alt_data/config/settings.py#L9).
- Production Compose correctly requires a password and binds to loopback: [docker-compose.server.yml](../docker-compose.server.yml#L8) and [line 13](../docker-compose.server.yml#L13).

**Why it matters**

Running the development command on a laptop attached to an untrusted network can expose a database guarded by a known fallback password. The safer production configuration does not eliminate this local foot-gun.

**Smallest reasonable correction**

Require `POSTGRES_PASSWORD` in every Compose profile, bind local development PostgreSQL to `127.0.0.1` by default, and make an intentionally public bind an explicit opt-in. Fail settings validation for the known placeholder password outside an explicitly marked test environment.

### P2 — Documentation overstates completion and operational proof

**Evidence**

- [README.md](../README.md#L80) calls Phases 1 and 2 complete and presents point-in-time work as the next milestone.
- [docs/PROJECT_NOTES.md](PROJECT_NOTES.md#L13) labels ingestion, economic, transportation, and market work complete.
- The same notes still list camera timer observation, market timer observation, and reboot recovery as unchecked at [lines 128-130](PROJECT_NOTES.md#L128).
- The repository contains no committed evidence of a successful scheduled market execution, restore test, or point-in-time join test.

**Why it matters**

Portfolio reviewers discount claims that are stronger than the committed proof. “Complete” should mean a precise acceptance criterion passed, not that a component once ran manually.

**Smallest reasonable correction**

Replace broad completion labels with evidence-based states such as “implemented,” “manually validated,” and “operational acceptance pending.” Add a concise acceptance checklist that links to reproducible commands, test output, and run artifacts without committing private production logs.

### P3 — Schema and audit tables lack several useful constraints and indexes

**Evidence**

- `IngestionRun.status` is an unconstrained string in [src/alt_data/models/all_models.py](../src/alt_data/models/all_models.py).
- Foreign keys such as `raw_payloads.ingestion_run_id` and `ingestion_runs.source_id` have no explicit indexes in the model.
- `RawPayload.payload` is truncated at [src/alt_data/ingestion/base.py](../src/alt_data/ingestion/base.py#L55), but no `truncated` flag or content hash is stored.

**Why it matters**

Bad status values can enter the audit trail, operational queries will slow as run history grows, and a reviewer cannot distinguish a complete raw body from an intentionally truncated one without comparing lengths manually. These are not immediate correctness failures but reduce durability and operability.

**Smallest reasonable correction**

Use a check constraint or enum for run status, add indexes for operational query paths, and store payload hash plus a boolean truncation indicator. Add retention rules for raw data.

### P3 — Dependency, build, and CI reproducibility are minimal

**Evidence**

- [requirements.txt](../requirements.txt) uses broad compatible version ranges without a lock file or hashes.
- [Dockerfile](../Dockerfile) installs those live ranges at image build time.
- No CI workflow is present in the repository, and no test/lint/type-check command is enforced before merge.

**Why it matters**

The same commit can build against different transitive dependencies over time. A portfolio project for production infrastructure should demonstrate that verification is repeatable and automatically enforced.

**Smallest reasonable correction**

Adopt a locked dependency workflow, pin the base image to a supported digest or clearly managed version, and add CI that runs formatting/linting, type checking, unit tests, PostgreSQL migration tests, and a Compose smoke test.

## Engineering review

### Data integrity and transactions

The base lifecycle correctly commits the initial run before the network request and marks failures after rollback. That is a worthwhile pattern. However, the final save transaction combines raw payload, transformation, many row inserts, and success status without bulk conflict handling. It is correct for one tiny serial job but has race conditions, N+1 existence checks, and no PostgreSQL integration coverage.

The greatest integrity concern is semantic, not SQL syntax: revision-sensitive economic data is skipped and daily market availability is not represented. A quantitative reviewer will view those as more serious than simple duplicate bugs.

### Provider isolation and failure handling

The adapters are a good direction: callers do not persist provider-specific JSON objects. The boundary needs to preserve real request/response provenance, parse provider error bodies safely, and handle each market symbol independently. Travel Midwest failure handling appropriately avoids an immediate retry, but the five-minute guard is not durable across service invocations.

### Timestamps and look-ahead risk

UTC-aware timestamps are consistently attempted, which is good. The project has `observed_at`, `published_at`, and `retrieved_at` fields but not a documented invariant for each source or a query layer that makes leakage hard. At present, the fields are recording aids, not a point-in-time system.

### Deployment, operations, and security

The production Compose file’s loopback binding, health check, persistent volume, systemd scheduling, and Tailscale-based access are real operational positives. Weaknesses are no declared service user, no alerts, no resource limits, manual/no-off-host backups, no restore test, and different security defaults in development Compose. The server is a useful homelab deployment, not a demonstrated production deployment yet.

### Testing quality

Nine fast unit tests are better than no tests, but they are all narrow. They do not test actual migrations, PostgreSQL behavior, scheduling, actual provider HTTP handling, error persistence, or restore. The suite should be described as parser/save unit coverage, not production verification.

## Scorecard

| Dimension | Score | Reviewer rationale |
| --- | ---: | --- |
| Architecture and separation of concerns | 6.5 | Clear `BaseIngestor` and provider adapters, but duplicated source registries and mixed current/latest-state semantics. |
| Data integrity and idempotency | 4.5 | Useful unique keys, but revision loss, race-prone check-then-insert, and migration drift are material. |
| Point-in-time research correctness | 3.0 | Fields exist, but availability/vintage semantics and leakage controls do not. |
| Reliability and failure handling | 5.0 | Failure runs are recorded and camera avoids immediate retry; no durable rate gate, per-symbol isolation, alerts, or tested recovery. |
| Security | 5.5 | Production DB is private and secrets are ignored; development defaults and root-run jobs are weak. |
| Observability and operations | 5.0 | Logs, run table, health script, and backup helper exist; no metrics/alerts/restore test. |
| Testing and CI | 3.5 | 9 passing narrow SQLite unit tests; no Postgres/migration/CI/deployment coverage. |
| Deployment reproducibility | 5.5 | Docker/systemd documentation is useful, but metadata-driven migrations, floating dependencies, and no CI reduce confidence. |
| Documentation and portfolio communication | 6.0 | Honest learning journal and useful commands, but completion claims exceed repository proof. |
| **Overall** | **5.6** | Strong learning prototype; not yet a production-grade quantitative data platform. |

## Recruiter reaction

“This candidate has actually deployed a data collector, used PostgreSQL, thought about provider limits, and can talk through Docker/systemd/Tailscale. That is stronger than a notebook-only project. I would be interested in a screen for an early-career infrastructure role. However, the README’s production/research-ready implications are ahead of the implementation. I would probe data-vintage correctness, migrations, backup recovery, and monitoring before treating this as evidence of production ownership.”

## Red flags

1. Claiming point-in-time correctness before availability-time and vintage data are implemented.
2. Claiming robust idempotency while relying on serial check-then-insert behavior.
3. Claiming provider-rate enforcement while the limiter resets with every container process or reboot.
4. Calling manual local dumps a backup strategy without a tested restore or off-host copy.
5. Calling tests comprehensive when they do not run migrations or PostgreSQL.
6. Calling milestones complete without committed operational acceptance evidence.

## Interview attack: 10 difficult questions

1. A GDP value for 2021 is revised in 2024. Which value is eligible for a strategy decision made in 2022, and where is that proven in your schema?
2. Why is a Yahoo daily bar’s `observed_at` not necessarily the time at which a trading system could use its close?
3. Show how two concurrently running market jobs avoid a duplicate-insert race without relying on application-level `SELECT` checks.
4. Why are your Alembic migrations importing current ORM metadata, and how can you prove a fresh database and an upgraded old database have identical schemas?
5. What prevents a server reboot two minutes after a camera request from violating the provider’s five-minute request rule?
6. Restore last night’s database backup into an isolated database. What are your RPO, RTO, and evidence that the dump is valid?
7. What happens when ticker 4 of 21 fails at the provider? Which tickers update, which fail, and how are you alerted?
8. Why are production job containers effectively root, and what least-privilege model would you use instead?
9. Your raw payload points to a Yahoo history page, not the chart endpoint actually called. How would an investigator replay the source request exactly?
10. Which tests would fail if PostgreSQL rejects a constraint that SQLite accepts differently, or if an Alembic migration does not match the ORM model?

## Path to 8.0

1. Make migrations explicit and immutable; test all upgrades on disposable PostgreSQL.
2. Implement source-specific availability/vintage semantics and a single tested point-in-time query boundary.
3. Use PostgreSQL conflict-safe bulk writes and test concurrent ingestion.
4. Persist provider request timing so rate limits survive containers and reboot.
5. Add CI with PostgreSQL integration tests, migration tests, and Compose smoke tests.
6. Add per-provider/job metrics and failure notification.
7. Implement scheduled, off-host, encrypted backups and one tested restore procedure.
8. Correct documentation to distinguish implemented, manually validated, and operationally accepted work.

## Path to 9.0

1. Add a tested research data contract: release calendar/source availability, revision vintages, and no-leakage feature generation.
2. Add durable orchestration/queueing or a carefully engineered scheduler with job locks, backpressure, per-source policies, and replay support.
3. Add immutable raw-response object storage with hashes, retention, versioned schemas, and lineage from raw response to normalized row.
4. Instrument latency, freshness, success rate, duplicate rate, provider error rate, database health, backup age, and restore success; alert on defined SLOs.
5. Harden runtime identity, image/dependency supply chain, secrets delivery, database role separation, and infrastructure-as-code.
6. Demonstrate load tests, failure injection, disaster recovery, and a complete reproducible server bootstrap.

## Resume test

**Do not yet say:** “Built a production-ready point-in-time alternative-data platform for quantitative research.”

**Accurate today:** “Built and deployed a Dockerized PostgreSQL ingestion prototype that collects provider-isolated market and Chicago traffic-camera metadata, records ingestion runs and bounded payloads, and schedules jobs with systemd on Ubuntu.”

**After the 8.0 path:** “Built a point-in-time-aware market and transportation data platform with revision-aware provenance, PostgreSQL conflict-safe ingestion, tested migrations, monitoring, and verified backup recovery.”

## Interview value

This project is worth discussing now because it gives concrete stories about provider access, a real upstream HTTP 500, rate-limit policy, schema migration tradeoffs, Docker installation, SSH/Tailscale access, systemd timers, database persistence, and debugging scheduler failures. Its best value is showing learning velocity and operational curiosity. Its weak value is any claim that the current system has solved quantitative data correctness or production reliability. Be candid about the gaps and describe the ordered remediation plan above.

## Audit stop point

This audit intentionally makes no implementation changes. The next work should be selected from the P1 remediation path, starting with migration reproducibility and point-in-time/vintage semantics before adding more sources or modeling.
