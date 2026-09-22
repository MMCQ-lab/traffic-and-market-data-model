# Project Notes

This is the practical project journal: what has been built, what was learned while building it, and what comes next.

## Goal

Build a research-ready alternative-data platform that can eventually align transportation, weather, economic, and market data without using information before it would have been available. It is research infrastructure, not an automated trading system or financial advice.

## Current snapshot

Production update (2026-09-21): the database-backed request gate, stronger
validation, failure isolation, PostgreSQL CI, migrations through `0007`, and the
IYT anomaly quarantine are deployed. The latest verified market batch completed
all 21 symbols. The link-traffic adapter described below is new local work and
is not yet deployed.

| Area | Status | Evidence |
| --- | --- | --- |
| Ingestion foundation | Complete | PostgreSQL, SQLAlchemy, Alembic, source/run/payload audit trail, tests |
| Economic data | Complete | World Bank U.S. GDP ingestion; 66 observations in the initial run |
| Chicago transportation | In progress | Monthly camera reference directory deployed; link-traffic observation adapter prepared locally |
| Market data | Complete | 21-symbol daily OHLCV universe stored on the Ubuntu server |
| Ubuntu hosting | Operational baseline | Docker PostgreSQL and ingestion container deployed; access is available over Tailscale/SSH |
| Automatic schedules | Operational | Camera metadata monthly; weekday market timer after market close |
| Weather and temporal joins | Not started | Phase 4 |
| Camera image collection / YOLO | Not started | Deferred intentionally |

## What we built

### Phase 1 — ingestion foundation

- Created a Python application with PostgreSQL, SQLAlchemy, Alembic migrations, Docker Compose, logging, and tests.
- Added an audit trail for every ingestion attempt: `data_sources`, `ingestion_runs`, and `raw_payloads`.
- Added an idempotent World Bank GDP ingestion job. A rerun does not duplicate observations.
- Connected DBeaver to PostgreSQL for interactive querying.

### Phase 2 — Travel Midwest / IDOT camera metadata

- Registered for Travel Midwest access and used the supported `cameraInfo.csv` metadata feed.
- Stored camera metadata in normalized `traffic_cameras` and `transportation_sources` tables.
- Preserved location, direction, latitude, longitude, snapshot URL, age flags, optional video URL, and retrieval time.
- Ignored the deprecated `ImgPath` column; the supported `SnapShot` URL is used instead.
- Added duplicate handling for repeated rows in one feed and across later runs.
- Enforced the provider rule that camera metadata should be fetched no more than once every five minutes. After the provider confirmed that the camera directory changes infrequently, its schedule was reduced to 3:00 AM America/Chicago on the first day of each month. A timeout is recorded for the next scheduled attempt rather than retried immediately.
- First Ubuntu server run persisted 4,568 camera records, with 119 duplicate feed rows skipped.
- Travel Midwest confirmed that `WarningAge`, `TooOld`, and `AgeInMinutes` reflect file-transfer timestamps and are not reliable snapshot times. `VideoUrl` is a placeholder. These fields must not be used as traffic or availability signals.

### Phase 2B — timestamped link traffic

- Added a provider-isolated parser for the authenticated `LinkTrafficReport.xml.gz` format.
- Normalized source timestamps, travel time, speed, volume, occupancy, and congestion into existing provider-neutral traffic tables.
- Converted the documented speed unit from metres/second to mph explicitly.
- Filtered provider-declared invalid location/data statuses and malformed or out-of-range measurements.
- Added HTTP Basic Auth with a strict HTTPS `travelmidwest.com` destination check so credentials cannot leak through a bad endpoint configuration.
- Kept this job unscheduled until PostgreSQL validation, a single live smoke test, and storage/cardinality sizing are complete.
- Documented that the archive has five-minute snapshots back to 2004, with roughly 2019 onward best aligned to current formats; a narrow subset should be piloted before any bulk request.

### Phase 3A — market data

- Added a provider-isolated Yahoo Finance adapter, so database tables do not depend on Yahoo response structures.
- Stored daily OHLCV fields: `open`, `high`, `low`, `close`, `adjusted_close`, `volume`, `observed_at`, and `retrieved_at`.
- Added an idempotency constraint on source, symbol, and observation timestamp.
- Loaded 21 symbols on Ubuntu: `SPY`, `QQQ`, `DIA`, `IWM`, `IYT`, `XLI`, `AMZN`, `UPS`, `FDX`, `WMT`, `TGT`, `COST`, `XPO`, `CHRW`, `DAL`, `UAL`, `LUV`, `AAL`, `UNP`, `CSX`, and `JBHT`.
- Each initial symbol history contained 4,202 daily records, for 88,242 market-price rows total.

## What we learned

- **Git:** Git must be installed, configured with a name/email, and given a safe-directory exception when a repository is created by a different Windows account or sandbox. The project is pushed to GitHub and should be updated through `git pull` on Ubuntu.
- **Python environments differ by operating system:** Windows uses `./.venv/Scripts/python.exe`; Ubuntu uses `./.venv/bin/python`. The Docker deployment avoids relying on either host virtual environment for production jobs.
- **Docker is a deployment tool, not the database itself:** Docker runs PostgreSQL consistently on both Windows and Ubuntu. The data survives container restarts in the named `postgres_data` volume.
- **DBeaver should use an SSH tunnel:** PostgreSQL is bound to the Ubuntu server's loopback interface, so it is not exposed to the public internet. DBeaver reaches it through SSH instead.
- **Daily OHLC data is not intraday data:** daily high and low are the extrema for the full day, but the exact time they occurred is not available. Intraday analysis would need a separate, higher-frequency source.
- **Point-in-time research requires discipline:** `retrieved_at` records when the system actually received a datum. Future joins must use availability time rather than simply joining dates.
- **Source policies matter:** documented endpoints, credentials, rate limits, and field deprecations are part of the engineering design—not paperwork to bypass.

## Server and development workflow

```text
Windows — aliens-laptop
  VS Code development
  Tailscale client
          |
          | encrypted Tailscale network
          v
Ubuntu — aliensserver
  /opt/traffic-and-market-data-model
  Docker
    ├── PostgreSQL
    └── ingestion jobs
          |
          v
  persistent postgres_data volume
```

The server is reachable through Tailscale using `ssh mmcq@aliensserver`. VS Code Remote SSH can work directly against `aliensserver` for inspection and debugging. GitHub remains the reviewed, source-controlled path for deploying code changes: develop/test, commit/push, then pull and rebuild on the server.

## Useful commands

### Development machine

```powershell
git status
python -m pytest -q -p no:cacheprovider
git add .
git commit -m "Describe the change"
git push origin main
```

### Ubuntu server

```bash
cd /opt/traffic-and-market-data-model
git pull origin main
docker compose --env-file .env -f docker-compose.server.yml --profile jobs build ingestor
docker compose --env-file .env -f docker-compose.server.yml ps
bash deploy/check-server-health.sh
bash deploy/backup-postgres.sh
```

### Verify data on Ubuntu

```bash
docker compose --env-file .env -f docker-compose.server.yml exec db \
  psql -U alternative_data -d alternative_data -c \
  "SELECT symbol, COUNT(*) FROM market_prices GROUP BY symbol ORDER BY symbol;"
```

## Next milestones

1. Run isolated PostgreSQL validation for the link-traffic adapter, then perform one approved live smoke test without adding a timer.
2. Measure live feed row counts and storage growth; choose a collection interval consistent with provider policy.
3. Ask Travel Midwest for a narrow 2019+ historical pilot covering selected Chicago freight corridors rather than attempting the multi-terabyte archive.
4. Add a weather source with source timestamps and retrieval timestamps.
5. Design point-in-time-correct temporal joins across traffic, weather, and market data before modeling.

## Hosting operational-completion checklist

- [x] Tailscale remote access
- [x] SSH access
- [x] Docker installed
- [x] PostgreSQL running in Docker
- [x] Project deployed under `/opt/traffic-and-market-data-model`
- [x] Historical market data loaded
- [x] Travel Midwest camera metadata ingested
- [ ] Camera systemd timer enabled and observed
- [ ] Market systemd timer enabled and observed
- [ ] Server reboot tested with services and timers resuming

## Guardrails

- Do not commit `.env`, passwords, API credentials, or private keys.
- Do not expose PostgreSQL port 5432 publicly.
- Do not poll Travel Midwest camera metadata or individual images more than once every five minutes.
- Do not start YOLO or prediction modeling until the historical collection and point-in-time dataset are ready.
