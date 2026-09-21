# Quant DevOps / Production Engineering Portfolio Audit

**Audit date:** 2026-09-19
**Scope:** Fresh read-only review of this checkout. `.env`, credentials, tokens, and private keys were not read. No application code was changed.

## Verdict

**Implementation update — 2026-09-21:** See [hardening notes](HARDENING.md) for
the prepared database request gate, transaction evidence retention, market
validation/batch isolation, safer test databases, required PostgreSQL CI, and
image checks. Local result: 35 passed, 16 PostgreSQL tests skipped (no local
Docker/PostgreSQL). Production and rebuilt-image validation remain pending.
The score below is the historical review score; it has not been reassessed.

**OVERALL SCORE: 6.1 / 10**
**STATUS: NOT ACCEPTABLE**

This is a credible, unusually hands-on early-career prototype: a real PostgreSQL deployment, normalized schemas, reusable ingestion lifecycle, provider adapters, systemd jobs, and an isolated PostgreSQL migration suite. It is substantially stronger than a notebook or API demo.

It is not yet a production-quality quantitative-research data platform. Point-in-time availability semantics, economic-data vintages, durable provider-rate enforcement, Docker build-secret hygiene, and recovery/monitoring remain materially incomplete. Those gaps matter more than adding another feed or model.

## Verified strengths

- [`BaseIngestor`](../src/alt_data/ingestion/base.py) persists an ingestion run before network I/O and records success or failure.
- [`docker-compose.server.yml`](../docker-compose.server.yml) binds PostgreSQL only to loopback and has a health check.
- Market rows have a unique database key on `(source_id, symbol, observed_at)` in [`all_models.py`](../src/alt_data/models/all_models.py#L102) and migration `0004`.
- Historical migrations [`0001`](../alembic/versions/0001_initial_schema.py) and [`0002`](../alembic/versions/0002_transportation_schema.py) now use explicit DDL, not current ORM metadata.
- [`0005_market_price_not_null.py`](../alembic/versions/0005_market_price_not_null.py) checks incompatible NULL OHLCV data before changing constraints and does not recreate `market_prices`.
- [`test_migrations_postgres.py`](../tests/test_migrations_postgres.py) contains PostgreSQL-specific coverage for zero-to-HEAD, 0004-to-0005 preservation, safe NULL failure, final constraints, uniqueness, and ORM-metadata isolation.

## Findings

### P1 — Daily market bars lack an availability timestamp, enabling look-ahead bias

**Evidence:** [`yahoo_finance.py`](../src/alt_data/ingestion/market/yahoo_finance.py#L45) converts a daily-bar timestamp into `observed_at`; [`yahoo_finance.py`](../src/alt_data/ingestion/market/yahoo_finance.py#L130) always writes `published_at=None`; [`all_models.py`](../src/alt_data/models/all_models.py#L106) has no `available_at`; and no point-in-time query boundary exists. README says the fields support look-ahead controls ([`README.md`](../README.md#L13)), but code does not enforce this.

**Why it matters:** A daily high, low, close, and volume are only known after the session and source publication. A historical backfill makes old bars available later than their trading date. Joining on `observed_at` can give a model completed information before it existed.

**Smallest reasonable correction:** Define one daily-bar availability convention, persist an `available_at`/source-publication time separate from session date, and provide one tested query boundary requiring `available_at <= decision_time`. Test close, weekends, holidays, and historical backfills.

### P1 — World Bank revisions are discarded permanently

**Evidence:** [`world_bank.py`](../src/alt_data/ingestion/economic/world_bank.py#L46) skips an existing observation key; [`all_models.py`](../src/alt_data/models/all_models.py#L49) makes `(source, indicator, geography, observed_at)` unique; and [`world_bank.py`](../src/alt_data/ingestion/economic/world_bank.py#L56) writes `published_at=None`.

**Why it matters:** Macro series are revised. The system stores neither what was first available at a decision time nor a later corrected vintage, preventing revision-aware research and correction.

**Smallest reasonable correction:** Store observation period plus source publication/retrieval vintage separately. Preserve raw-payload provenance and select the latest vintage available at or before a requested decision time.

### P1 — Travel Midwest rate limiting is process-local, not durable across jobs or reboot

**Update:** Replaced locally by `PostgresRequestGate` plus migration `0006`,
with a committed claim and an advisory lock held during the request. Tests cover
process death, overlap, cooldown retention, and failure with missing schema.
Real PostgreSQL execution and production deployment are still pending; this
finding is implemented but not yet operationally closed.

**Evidence:** [`travel_midwest.py`](../src/alt_data/ingestion/traffic/travel_midwest.py#L25) stores last request time only in a class dictionary using `time.monotonic()`; each timer activation creates a short-lived container ([camera service](../deploy/systemd/alternative-data-camera.service#L8)); and the timer can run two minutes after boot ([camera timer](../deploy/systemd/alternative-data-camera.timer#L5)).

**Why it matters:** The in-memory guard disappears when the job/container ends or the host reboots. A manual run plus reboot, or concurrent activation, can violate the provider interval despite the normal hourly timer.

**Smallest reasonable correction:** Persist last attempt per endpoint transactionally in PostgreSQL and acquire that gate before requests. Test independent process instances and a simulated reboot. Keep the hourly timer as normal cadence.

### P1 — Docker builds could copy `.env` into the image **(remediated for new builds; pre-fix image must be retired)**

**Original evidence:** This checkout contains `.env` (not inspected); [`.gitignore`](../.gitignore) only governs Git; and [`Dockerfile`](../Dockerfile#L8) uses `COPY . ./`.

**Why it matters:** Docker does not honor `.gitignore`. Building from this directory can put credentials into the image filesystem/layers, where registry/image-export access can expose them.

**Implemented correction:** [`.dockerignore`](../.dockerignore) now excludes `.env`/`.env.*`, VCS metadata, virtual environments, Python/test artifacts, local data/backup/work outputs, database dump patterns, and editor/OS artifacts. It deliberately retains application source, migrations, requirements, Compose files, tests, and documentation. Compose still supplies runtime configuration through `env_file: .env` in [`docker-compose.server.yml`](../docker-compose.server.yml).

**Verification evidence:** On `aliensserver`, a clean `docker build --no-cache -t alt-data-context-audit:local .` succeeded with a 24.23 kB build context. The resulting image passed assertions that `/app/.env`, `/app/.git`, `/app/.venv`, `/app/.pytest_cache`, `/app/data`, and `/app/backups` are absent, while application source and migration `0005_market_price_not_null.py` are present. No secret values were printed. The production Compose configuration remains unchanged and continues to provide runtime configuration through `env_file: .env`; its runtime smoke output should be retained with the deployment record.

**Pre-fix exposure evidence:** An earlier server build of `traffic-and-market-data-model-ingestor:latest` used a 2-byte `.dockerignore`, transferred a 13.80 MB build context, and used the broad `COPY . ./` Dockerfile instruction. Since Compose on that server uses a local `.env`, that previously built image may contain `/app/.env`. No secret value was inspected or printed. Rebuild the official `ingestor` tag from the corrected context before running another scheduled job, then retire the older image according to the server's normal image-cleanup procedure.

### P1 — Backup handling is manual, local-only, and lacks restore proof

**Evidence:** [`backup-postgres.sh`](../deploy/backup-postgres.sh#L18) writes a local dump; [`deploy/README.md`](../deploy/README.md#L61) documents creation while leaving restore manual. There is no backup timer, retention, checksum, encryption, off-host copy, restore drill, RPO/RTO, or automated test.

**Why it matters:** A same-host untested dump does not cover host loss, corruption, deletion, or an invalid backup. It is not evidence of recovery.

**Smallest reasonable correction:** Add a guarded restore runbook, scheduled retention, encrypted off-host copy, and recurring restoration into an isolated PostgreSQL instance. Record/alert on backup age and last restore verification.

### P2 — Idempotency is check-then-insert and races under concurrent jobs

**Evidence:** Yahoo checks then adds in [`yahoo_finance.py`](../src/alt_data/ingestion/market/yahoo_finance.py#L124); World Bank does the same in [`world_bank.py`](../src/alt_data/ingestion/economic/world_bank.py#L46); camera metadata selects before insert in [`cameras.py`](../src/alt_data/ingestion/traffic/cameras.py#L76). Constraints exist, but there is no `ON CONFLICT` or integrity-error recovery.

**Why it matters:** Two jobs can both see no row; one unique violation then rolls back the whole ingestion run in [`base.py`](../src/alt_data/ingestion/base.py#L63).

**Smallest reasonable correction:** Use PostgreSQL conflict-safe inserts/upserts and add a two-session PostgreSQL concurrency test.

### P2 — Camera ingestion stores current metadata only, not historical snapshots or versions

**Evidence:** Existing `TrafficCamera` rows are overwritten in place ([`cameras.py`](../src/alt_data/ingestion/traffic/cameras.py#L76)). `CameraSnapshot` exists ([`all_models.py`](../src/alt_data/models/all_models.py#L153)) but no active code inserts into it. README says image bytes are intentionally deferred ([`README.md`](../README.md#L56)).

**Why it matters:** The system cannot reconstruct earlier metadata or pixels. It is a current camera directory, not a historical transportation/CV dataset.

**Smallest reasonable correction:** Either clearly document current-metadata-only scope, or separately design an approved, rate-compliant snapshot/version pipeline with object storage, hashes, retention, and provider permission validation.

### P2 — Failures are recorded but not operationally observable

**Evidence:** [`base.py`](../src/alt_data/ingestion/base.py#L63) records failed runs and re-raises. Both systemd services are bare `Type=oneshot` units with no `User=`, timeout, resource hardening, failure hook, or alert ([`deploy/systemd`](../deploy/systemd)). [`check-server-health.sh`](../deploy/check-server-health.sh) is on-demand only.

**Why it matters:** Failures can persist until a human reads a journal. There is no freshness/SLO signal; systemd does not retry the one-shot jobs; and default system service identity is normally root.

**Smallest reasonable correction:** Use a dedicated least-privilege user with timeouts/hardening. Define freshness/failure SLOs, durable status/metrics, and alerts. Preserve the correct no-aggressive-retry policy for Travel Midwest.

### P2 — Tests are uneven; real PostgreSQL migration validation is not mandatory in the ordinary local run

**Update:** Added lifecycle and gate integration tests, schema/model parity,
actual uniqueness enforcement, test-database guards, and GitHub Actions using
`deploy/validate-isolated.sh`. Required CI mode fails on skips. The original
source text below describes the audit baseline; CI execution is pending push.

**Evidence:** This audit's virtual-environment run produced **11 passed, 4 skipped**; the four skips were [`test_migrations_postgres.py`](../tests/test_migrations_postgres.py) because no Docker daemon was available. Other persistence tests use SQLite `Base.metadata.create_all()` (for example [`test_yahoo_finance.py`](../tests/test_yahoo_finance.py#L40)). No tracked CI workflow exists. There is no test of `BaseIngestor.run()` transaction paths, provider auth failure, Compose, systemd, backup, or restore.

**Why it matters:** The migration suite is a strong improvement, but the application/deployment behavior most likely to differ on PostgreSQL is not continuously required.

**Smallest reasonable correction:** Make the existing PostgreSQL suite required in CI, then add focused lifecycle/conflict tests, Compose smoke testing, and disposable backup/restore validation.

### P2 — Development configuration has unsafe defaults and operational scripts assume fixed names

**Evidence:** [`docker-compose.yml`](../docker-compose.yml#L8) publishes PostgreSQL on all interfaces by default; it has a fallback password ([line 6](../docker-compose.yml#L6)), as does [`settings.py`](../src/alt_data/config/settings.py#L10). [`check-server-health.sh`](../deploy/check-server-health.sh#L25) hardcodes the database/user instead of using configuration.

**Why it matters:** The production Compose file is private, but a developer can accidentally expose a predictable-password development database. Health checks can fail or misreport after a legitimate configuration change.

**Smallest reasonable correction:** Require a password, bind development PostgreSQL to loopback by default, and read configured database names safely without printing secrets.

### P2 — Documentation conflicts with implemented state

**Evidence:** README calls Phases 1 and 2 complete ([`README.md`](../README.md#L78)) and describes timestamp fields as distinguishing availability ([line 13](../README.md#L13)). [`PROJECT_NOTES.md`](PROJECT_NOTES.md#L18) still calls scheduling the next gate and leaves timer/reboot validation unchecked at lines 128–130. Active market and World Bank ingestors write `published_at=None`.

**Why it matters:** Portfolio reviewers penalize claims stronger than code. Conflicting documents obscure what was demonstrated versus planned.

**Smallest reasonable correction:** Maintain one concise status table separating implemented, validated, deployed, and planned. Explicitly state that vintages, point-in-time joins, snapshots, recovery testing, and monitoring are incomplete.

### P3 — Dependency and build reproducibility are weak

**Evidence:** [`requirements.txt`](../requirements.txt) uses broad ranges without a lock file/hashes. [`Dockerfile`](../Dockerfile) uses a floating `python:3.12-slim` tag and unrestricted package resolution. No CI/build verification is tracked.

**Why it matters:** Future rebuilds can select different dependencies or base images from the same commit.

**Smallest reasonable correction:** Add a locked dependency artifact with hashes, a maintained base-image pin/digest policy, and CI build/test verification.

### P3 — The schema lacks some operational indexes and validity constraints

**Evidence:** [`all_models.py`](../src/alt_data/models/all_models.py) has unique constraints but no explicit indexes for common foreign keys such as `ingestion_runs.source_id` and `raw_payloads.ingestion_run_id`. `IngestionRun.status` is unconstrained ([line 27](../src/alt_data/models/all_models.py#L27)) and counters have no non-negative checks.

**Why it matters:** Run/payload queries will slow as collection grows and invalid lifecycle values are representable.

**Smallest reasonable correction:** Add only query-driven indexes and lightweight status/counter constraints, with migration tests.

## Remediated migration finding

**New parity issue found and corrected in forward migration `0007`:** The ORM
inherits `traffic_cameras.created_at`, but explicit historical `0002` omitted
that column. `0007` backfills from `first_seen_at` only when the column is missing
and preserves existing legacy values. A whole-model column/nullability test and
both camera upgrade cases now cover this gap. `0001`–`0005` remain unchanged.

The previous migration-reproducibility P1 is **verified remediated in this checkout**. `0001` and `0002` contain immutable explicit DDL; `0005` safely reconciles nullable OHLCV fields; and the Postgres-only test suite guards historical migration isolation and upgrade behavior. The local audit environment could not execute Docker-dependent tests, so their source was inspected and execution remains required in Docker/CI. This is no longer an open migration-design finding.

## Engineering review

### Data integrity and timestamp semantics

The schema has the right vocabulary—`observed_at`, `published_at`, and `retrieved_at`—and UTC-aware columns. The active sources do not define/enforce a source-specific availability invariant. `retrieved_at` is evidence of local receipt, not publication time, and cannot make a historical backfill point-in-time correct. Constraints help, but check-then-insert forfeits concurrency safety.

### Provider isolation and failure handling

The Yahoo adapter keeps provider JSON outside the data model, a sound prototype boundary. HTTP timeouts and status checks exist. Yahoo/World Bank have no retry/backoff policy or health telemetry; one failing Yahoo symbol ends the sequential loop. Travel Midwest correctly avoids immediate retry, but its rate gate is not durable.

### Docker, systemd, security, and recovery

Loopback PostgreSQL, health checks, named volume, systemd scheduling, and Tailscale-oriented access are real strengths. The absent `.dockerignore` is a material secret risk. Systemd is minimally configured. The backup helper is safe in the narrow sense that it does not overwrite data, but is not a recovery program.

### Performance and complexity

The design is compact and understandable. Primary scale risks are per-row existence reads for historical load and missing measured indexes. Fix data correctness and operability before adding queues, Kubernetes, or a data lake.

## Scorecard

| Area | Score | Assessment |
| --- | ---: | --- |
| Data correctness / point-in-time safety | 3.0 | Timestamp fields exist; availability and vintages do not. |
| Database design and migrations | 7.5 | Good foundation; migration reproducibility has strong PostgreSQL-specific coverage. |
| Ingestion architecture | 6.5 | Reusable lifecycle/adapters; concurrency and resilience incomplete. |
| Testing and verification | 6.0 | Offline unit tests and migration suite; no CI/lifecycle/deployment/restore coverage. |
| Security and deployment | 5.0 | Private production port is good; build-secret exposure/hardening remain serious. |
| Observability and recovery | 4.5 | Logs, runs, health script, dump helper; no metrics, alerts, or verified restore. |
| Documentation / portfolio communication | 5.0 | Helpful narrative but readiness claims exceed implementation. |

## Recruiter reaction

“This candidate has deployed a real collector, used PostgreSQL, Docker, systemd, and provider constraints, and can explain a safe schema-migration remediation. That is stronger than a notebook-only project. I would consider an early-career screen, but not accept ‘production-ready’ or ‘point-in-time research-ready’ claims until availability/vintage correctness, recovery, and monitoring are demonstrated.”

## Red flags

1. Claiming daily OHLCV is point-in-time usable without an availability convention.
2. Discarding macro revisions while claiming reproducible historical information sets.
3. Provider-rate enforcement that resets with each container or reboot.
4. A Dockerfile that can bake `.env` into image layers.
5. Calling manual local dumps a recovery strategy without a restore drill.
6. Conflicting documentation about operational completion.

## Interview attack: 10 difficult questions

1. At what timestamp may a strategy use a Yahoo daily close, and how is that enforced?
2. How would you reconstruct GDP as it was known before a later revision?
3. What prevents two systemd runs inserting a duplicate price simultaneously?
4. Why does `.gitignore` not protect `.env` in a Docker build, and how prove the fix?
5. How does a reboot two minutes after a camera request avoid violating provider policy?
6. Restore last night's backup into isolation: what are measured RPO, RTO, and validation checks?
7. Which parts of `BaseIngestor.run()` commit if parsing fails, and why?
8. Why are these migration tests PostgreSQL-specific rather than SQLite?
9. How would you monitor stale data and repeated provider failures without logging secrets?
10. Why is a historical Yahoo backfill riskier than live collection for a quant model?

## Path to 8.0

1. Fix Docker build-context secret handling and prove it.
2. Implement explicit availability/vintage semantics plus one tested point-in-time query boundary.
3. Make provider timing durable and conflict-safe across processes/reboots.
4. Use PostgreSQL conflict-safe writes and test concurrency.
5. Run unit tests and PostgreSQL migrations in CI.
6. Add scheduled encrypted off-host backups and demonstrate isolated restore.
7. Add freshness/failure monitoring and a clear operational runbook.

## Path to 9.0

1. Add revision-aware economic/market provenance with source hashes and deterministic replay.
2. Add a tested weather source and point-in-time joins.
3. Harden deployment with least privilege, timeouts, limits, and pinned supply chain.
4. Define SLOs for ingestion, freshness, DB health, backups, and recovery; exercise alerts.
5. Run load/concurrency tests and tune indexes from query plans.
6. Design camera snapshot storage only after permission, lifecycle, cost, and recovery requirements are explicit.

## Resume test

**Accurate now:** “Built and deployed a PostgreSQL-based market and transportation ingestion prototype using Docker, systemd, SQLAlchemy/Alembic, provider adapters, and tested PostgreSQL schema migrations.”

**Do not claim yet:** “Built a production-ready point-in-time alternative-data platform,” “eliminated look-ahead bias,” or “implemented disaster recovery.”

**After the 8.0 path:** “Built a point-in-time-aware data platform with revision-aware provenance, conflict-safe PostgreSQL ingestion, verified migrations, monitoring, and tested backup recovery.”

## Interview value

The project already supports good stories about provider integration, upstream failures, Docker deployment, SSH/Tailscale access, scheduler behavior, and safe migration repair. Its value increases if you lead with what is implemented, state the gaps candidly, and explain the remediation order. The next most valuable work is correctness and operability—not another data source, YOLO, or model.

## Test record for this audit

Executed locally with the repository virtual environment:

```text
11 passed, 4 skipped in 1.26s
```

All four skips were intentionally PostgreSQL-only migration tests, skipped because this audit workstation had no Docker daemon. No production database, server, external provider, `.env`, credentials, or private key was accessed.
