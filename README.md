# Alternative Data Platform

Phase 1 is a small, reliable ingestion foundation for public alternative and macroeconomic data. It currently ingests U.S. annual real GDP from the no-authentication [World Bank Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api).

It deliberately does **not** include computer vision, forecasting, trading, or a dashboard.

## Architecture

`World Bank API -> WorldBankGdpIngestor -> validation/transformation -> PostgreSQL`

Each execution records a source-specific `ingestion_run`, metadata plus a bounded raw response in `raw_payloads`, and normalized values in `economic_indicators`. All system timestamps are UTC. The `published_at`, `observed_at`, and `retrieved_at` fields distinguish source availability, the event period, and our receipt time to support later look-ahead-bias controls.

## Schema

- `data_sources`: registered providers and base URLs.
- `ingestion_runs`: auditable job lifecycle, counters, and errors.
- `raw_payloads`: response metadata and optional bounded payload; external object paths are ready for images/large files.
- `economic_indicators`: normalized observations, unique by source/indicator/geography/period.
- `camera_locations`, `traffic_camera_observations`, `roadway_sensor_observations`, `weather_observations`, and `market_prices`: initial extensibility tables.

## Prerequisites and database

Install Python 3.11+ and PostgreSQL 15+ (or Docker). Copy the environment template and set a password:

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
pytest
```

The run should report one successful ingestion run, one raw payload record, and approximately 60+ annual GDP observations (the exact count can change with upstream revisions). Run the command again: it should complete successfully and report existing observations as skipped, without duplicates.

## Next step

Add one operationally useful source—such as National Weather Service observations or a selected city DOT camera feed—by subclassing `BaseIngestor`, then add its migration and transformation tests.
