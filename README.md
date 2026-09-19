# Alternative Data Platform

For a running project journal, current milestones, learned setup notes, and the server workflow, see [Project Notes](docs/PROJECT_NOTES.md).

Phase 1 is a small, reliable ingestion foundation for public alternative and macroeconomic data. Phase 2 adds normalized transportation metadata and a live Travel Midwest / IDOT Gateway camera feed.

It deliberately does **not** include computer vision, forecasting, trading, or a dashboard.

## Architecture

`Public API/feed -> ingestor -> validation/transformation -> PostgreSQL`

Each execution records a source-specific `ingestion_run`, metadata plus a bounded raw response in `raw_payloads`, and normalized values in `economic_indicators`. All system timestamps are UTC. The `published_at`, `observed_at`, and `retrieved_at` fields distinguish source availability, the event period, and our receipt time to support later look-ahead-bias controls.

## Schema

- `data_sources`: registered providers and base URLs.
- `ingestion_runs`: auditable job lifecycle, counters, and errors.
- `raw_payloads`: response metadata and optional bounded payload; external object paths are ready for images/large files.
- `economic_indicators`: normalized observations, unique by source/indicator/geography/period.
- `camera_locations`, `traffic_camera_observations`, `roadway_sensor_observations`, `weather_observations`, and `market_prices`: initial extensibility tables.
- `transportation_sources`, `traffic_cameras`, `camera_snapshots`, `traffic_sensors`, `traffic_observations`, `transportation_incidents`, and `construction_events`: Phase 2 transportation tables.

## Prerequisites and database

Install Python 3.11+ and Docker Desktop (or PostgreSQL 15+). Copy the environment template and set a password:

```powershell
Copy-Item .env.example .env
docker compose up -d db
```

For an existing PostgreSQL installation, create the configured database and user, then set `POSTGRES_*` in `.env`. `DATABASE_URL` overrides the separate PostgreSQL variables when supplied.

## Install and migrate

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
```

## Run and verify

```powershell
python -m scripts.ingest_world_bank_gdp
python -m scripts.verify_data
python -m pytest -q -p no:cacheprovider
```

The run should report one successful ingestion run, one raw payload record, and approximately 60+ annual GDP observations (the exact count can change with upstream revisions). Run the command again: it should complete successfully and report existing observations as skipped, without duplicates.

## Phase 2: Chicago transportation ingestion

The transportation layer adds provider-neutral tables for `transportation_sources`, `traffic_cameras`, `camera_snapshots`, `traffic_sensors`, `traffic_observations`, `transportation_incidents`, and `construction_events`. Source timestamps, publication timestamps, and retrieval timestamps are separate UTC fields where the feed supplies them. Camera image bytes are intentionally not stored in PostgreSQL; `camera_snapshots.storage_path` is reserved for files under `CAMERA_STORAGE_PATH`.

Travel Midwest / IDOT Gateway is the primary Chicago live source. Registration is required for its XML and image feeds. Configure the returned credentials in `.env`; never commit them. The public camera metadata CSV endpoint is `https://travelmidwest.com/lmiga/cameraInfo.csv`. The IDOT reuse policy requires each XML feed and individual camera image to be requested no more than once every five minutes and requires attribution: “Gateway traffic information courtesy of the Illinois Department of Transportation.” The client enforces a process-level five-minute minimum interval, does not immediately retry failed feed requests, and refuses to run without a configured feed URL.

After registering, set `TRAVEL_MIDWEST_CAMERA_FEED_URL=https://travelmidwest.com/lmiga/cameraInfo.csv` in `.env` and run:

```powershell
python -m scripts.ingest_travel_midwest_cameras
```

The camera CSV's supported `SnapShot` field is stored as `traffic_cameras.image_url`; `ImgPath` is intentionally ignored because Travel Midwest deprecated it. `WarningAge`, `TooOld`, `AgeInMinutes`, and `VideoUrl` are preserved as metadata. Normal tests use XML and CSV fixtures and never call Travel Midwest. Before live use, confirm the registration-provided endpoint and field names against the supplied documentation. Example DBeaver queries:

```sql
SELECT COUNT(*) AS camera_count FROM public.traffic_cameras;
SELECT external_camera_id, name, direction, latitude, longitude, image_url
FROM public.traffic_cameras ORDER BY name, direction LIMIT 20;
SELECT status, rows_received, rows_inserted, rows_skipped, error_message
FROM public.ingestion_runs ORDER BY started_at DESC LIMIT 5;
```

The City of Chicago Open Data portal may supplement this source for historical or enforcement-camera metadata, but it is not a substitute for live IDOT monitoring cameras. Live camera images remain subject to provider permissions, rate limits, and storage policy.

## Current status

Phases 1 and 2 are complete. Phase 3A has loaded the initial market universe on the Ubuntu server. Camera image download/storage and computer vision remain intentionally deferred; weather ingestion and point-in-time temporal joins are the next data milestones.

## Linux server operation

For a continuously running Linux-hosted PostgreSQL database and scheduled ingestion jobs, follow [the server deployment guide](deploy/README.md). The server configuration uses Docker for PostgreSQL and systemd timers for jobs; it keeps the database private to the server and does not expose credentials in Git.

## Phase 3A: market data (SPY)

Market ingestion is provider-isolated behind `YahooFinanceMarketIngestor`; the database stores normalized OHLCV values and never depends on Yahoo-specific objects. The initial universe is `SPY, QQQ, DIA, IWM, IYT, XLI`, plus `AMZN, UPS, FDX, WMT, TGT, COST, XPO, CHRW`, and representative airlines/transportation companies `DAL, UAL, LUV, AAL, UNP, CSX, JBHT`. Daily history begins in 2010 by default. Set `MARKET_SYMBOLS`, `MARKET_START_DATE`, or `MARKET_END_DATE` in `.env` to change the run. Yahoo Finance is a research-prototype source and may change availability or terms.

After installing the updated requirements and applying migrations, run:

```powershell
pip install -r requirements.txt
alembic upgrade head
python -m scripts.ingest_yahoo_finance
```

Verify in DBeaver:

```sql
SELECT COUNT(*) AS price_count,
       MIN(observed_at) AS first_date,
       MAX(observed_at) AS last_date
FROM public.market_prices;

SELECT symbol, observed_at, open, high, low, close, adjusted_close, volume
FROM public.market_prices
ORDER BY observed_at DESC
LIMIT 20;
```

Run the command a second time to confirm `rows_inserted = 0` and `rows_skipped` equals the existing history. Do not build temporal joins or predictive models until this single-instrument path is verified.

## Next step

Add one operationally useful source—such as National Weather Service observations or a selected city DOT camera feed—by subclassing `BaseIngestor`, then add its migration and transformation tests.
