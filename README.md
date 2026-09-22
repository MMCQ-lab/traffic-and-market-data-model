# Alternative Data Platform

For a running project journal, current milestones, learned setup notes, and the server workflow, see [Project Notes](docs/PROJECT_NOTES.md).

This is a deployed research prototype for public alternative and macroeconomic data, camera metadata, and daily market prices. See [the engineering review](docs/REVIEW.md) for remaining reliability and research-data limitations.

It deliberately does **not** include computer vision, forecasting, trading, or a dashboard.

## Architecture

`Public API/feed -> ingestor -> validation/transformation -> PostgreSQL`

Ingestion records a source-specific `ingestion_run` before fetching. A successful fetch is committed to `raw_payloads` before validation, so its bounded payload survives validation/write failures. Normalized observations and successful run status commit together; partial observation writes roll back on failure. Yahoo's payload is the adapter's normalized JSON, not the original HTTP response. Payloads larger than the configured limit are truncated and cannot serve as a complete replay archive.

`observed_at` is the observation period, and `retrieved_at` is our receipt time. Market and GDP `published_at` values are currently unknown. These tables **are not yet point-in-time research datasets**: availability rules, revised data vintages, and a tested as-of query boundary remain to be implemented.

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

Travel Midwest / IDOT Gateway is the primary Chicago live source. Registration is required for its XML and image feeds. Configure the returned credentials in `.env`; never commit them. The public camera metadata CSV endpoint is `https://travelmidwest.com/lmiga/cameraInfo.csv`. The IDOT reuse policy requires each XML feed and individual camera image to be requested no more than once every five minutes and requires attribution: “Gateway traffic information courtesy of the Illinois Department of Transportation.”

The client uses a PostgreSQL-backed, provider-wide gate with a minimum 300-second cooldown. It commits the claim before feed I/O, holds an advisory lock during the request, and records completion even after an HTTP failure. Separate jobs using the same database share this state; rolling back ingestion does not erase the claim. Missing gate schema or database errors prevent feed requests. Migration `0006` is required before enabling this client. Do not run parallel collectors against separate databases: they do not share a gate. The public camera directory is fetched without login. Authenticated XML downloads use HTTP Basic Auth and refuse to send credentials to any host other than HTTPS `travelmidwest.com`.

After registering, set `TRAVEL_MIDWEST_CAMERA_FEED_URL=https://travelmidwest.com/lmiga/cameraInfo.csv` in `.env` and run:

```powershell
python -m scripts.ingest_travel_midwest_cameras
```

The camera CSV's supported `SnapShot` field is stored as `traffic_cameras.image_url`; `ImgPath` is intentionally ignored because Travel Midwest deprecated it. `WarningAge`, `TooOld`, and `AgeInMinutes` are preserved only for source fidelity: the provider says they reflect file-transfer timestamps and are not reliable snapshot times. `VideoUrl` is a placeholder and is not a research signal. Camera metadata is a slowly changing reference directory, scheduled monthly, not a traffic time series. Normal tests use XML and CSV fixtures and never call Travel Midwest. Example DBeaver queries:

```sql
SELECT COUNT(*) AS camera_count FROM public.traffic_cameras;
SELECT external_camera_id, name, direction, latitude, longitude, image_url
FROM public.traffic_cameras ORDER BY name, direction LIMIT 20;
SELECT status, rows_received, rows_inserted, rows_skipped, error_message
FROM public.ingestion_runs ORDER BY started_at DESC LIMIT 5;
```

The City of Chicago Open Data portal may supplement this source for historical or enforcement-camera metadata, but it is not a substitute for live IDOT monitoring cameras. Live camera images remain subject to provider permissions, rate limits, and storage policy.

### Link traffic observations

`TravelMidwestLinkTrafficIngestor` is the next transportation-data adapter. It parses the documented authenticated `LinkTrafficReport.xml.gz` feed into provider-neutral `traffic_sensors` and `traffic_observations`. The adapter keeps the source observation timestamp separate from `retrieved_at`, converts documented metres/second to mph, and stores travel time (seconds), volume (vehicles/lane/hour), occupancy percentage, and congestion status. Records with provider-declared invalid data/location status, unknown congestion, missing IDs, invalid timestamps, nonfinite measurements, negative values, or occupancy outside 0–100 are rejected and counted in logs. The bounded raw XML is committed before validation.

Provider references: [traffic report fields](https://github.com/uic-gtis/gateway-docs/blob/main/user-guides-and-manuals/traffic-reports.md) and [historical archive](https://github.com/uic-gtis/gateway-docs/blob/main/user-guides-and-manuals/gateway-traffic-data-archive.md).

Set `TRAVEL_MIDWEST_TRAFFIC_FEED_URL` only after confirming the approved account and endpoint, then perform a single manual run:

```powershell
python -m scripts.ingest_travel_midwest_link_traffic
```

This job is intentionally **not scheduled or deployed yet**. Fixture tests do not contact the provider. A production timer should be added only after isolated PostgreSQL validation, one rate-compliant live smoke test, volume sizing, and confirmation of the desired collection interval. The provider archive contains the same report family at roughly five-minute intervals; only the past 24 hours are directly downloadable, while historical subsets require coordination with Travel Midwest.

## Current status

GDP, monthly camera-reference ingestion, and the 21-symbol market batch are deployed; the latest verified market batch completed all 21 symbols. The link-traffic adapter is implemented locally but is not yet validated on PostgreSQL or deployed. Camera images, weather, historical traffic backfill, point-in-time joins, revision history, monitoring/alerts, and tested off-host recovery remain incomplete.

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

Run the command a second time to confirm `rows_inserted = 0` and `rows_skipped` equals the existing history. The batch tries all configured symbols with independent sessions and returns a nonzero exit code if any failed. Incomplete bars are logged/skipped; invalid OHLC ordering, nonfinite values, invalid volumes, and timezone-free timestamps are rejected. Missing adjusted closes are not replaced with unadjusted closes. Existing observations are still skipped rather than revised: this is not a vintage store, and simultaneous ingestors still need conflict-safe writes.

## Tests and isolated validation

Local tests disable dotenv loading before application imports. Run `python -m pytest -q -rs -p no:cacheprovider`. PostgreSQL tests may skip on a workstation without Docker; those skips do not establish migration correctness.

On Linux with Docker, run the same validation used by GitHub Actions:

```bash
bash deploy/validate-isolated.sh
```

This builds a temporary image, checks its filesystem and a synthetic runtime configuration override, starts an isolated PostgreSQL 16 instance with no published ports or production volumes, runs the entire suite with skips prohibited, and cleans up only its own resources. It does not load production `.env`, use production Compose, or deploy migrations to production. See [the hardening notes](docs/HARDENING.md) for deployment sequencing and limitations.

## Next step

Validate the link-traffic adapter in isolated PostgreSQL, perform one approved live smoke test, and size a narrow 2019+ historical pilot before enabling recurring traffic collection or modeling.
