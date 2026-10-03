# Linux server deployment

These commands assume an Ubuntu or Debian server and a repository path of `/opt/traffic-and-market-data-model`.

## One-time setup

Install Docker Engine and the Compose plugin using Docker's official instructions for your Linux distribution. Then clone the repository and create the server-only environment file:

```bash
sudo git clone https://github.com/MMCQ-lab/traffic-and-market-data-model.git /opt/traffic-and-market-data-model
sudo chown -R "$USER":"$USER" /opt/traffic-and-market-data-model
cd /opt/traffic-and-market-data-model
cp .env.example .env
chmod 600 .env
nano .env
```

Set a long, unique `POSTGRES_PASSWORD`. Add the Travel Midwest credentials and camera feed URL. Never commit `.env`.

Start the private database, build the ingestion image, and apply migrations:

```bash
docker compose --env-file .env -f docker-compose.server.yml up -d db
docker compose --env-file .env -f docker-compose.server.yml --profile jobs build ingestor
docker compose --env-file .env -f docker-compose.server.yml run --rm ingestor python -m alembic upgrade head
```

Run each job once before scheduling it:

```bash
docker compose --env-file .env -f docker-compose.server.yml run --rm ingestor python -m scripts.ingest_travel_midwest_cameras
docker compose --env-file .env -f docker-compose.server.yml run --rm ingestor python -m scripts.ingest_yahoo_finance
```

## Scheduled collection

```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now alternative-data-camera.timer alternative-data-market.timer
systemctl list-timers 'alternative-data-*'
```

The camera-metadata job runs at 3:00 AM America/Chicago on the first day of each month. The market job runs at 7:15 PM America/Chicago each weekday. The Travel Midwest five-minute rule remains the maximum permitted request frequency, not a requirement to poll that often.

### Roadway traffic observations: fifteen-minute pilot

This is separate from the monthly camera directory. Enable it only after the
traffic feed is configured and a manual `scripts.ingest_travel_midwest_link_traffic`
run succeeds. Operator-provided Ubuntu output for run 696 confirmed 469 inserted
observations on October 2, 2026, Chicago time (October 3 UTC). That proves the
manual path, not an installed or successfully recurring timer.

For the existing server, after pushing these files and pulling the same commit:

```bash
(
set -euo pipefail
cd /opt/traffic-and-market-data-model
git pull --ff-only origin main
sudo systemd-analyze verify deploy/systemd/alternative-data-traffic.service deploy/systemd/alternative-data-traffic.timer
sudo install -m 644 deploy/systemd/alternative-data-traffic.service deploy/systemd/alternative-data-traffic.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now alternative-data-traffic.timer
systemctl list-timers --all 'alternative-data-*' --no-pager
)
```

These commands install only the traffic units; they do not change camera/market
schedules, restart PostgreSQL, rebuild the already-working ingestion image, or run
migrations. Stop if verification or installation fails. Do not create a second
traffic schedule in n8n or cron alongside this timer.

The first run is due approximately 15 minutes after timer activation. Subsequent
runs are due 15 minutes after the service finishes, so there are slightly fewer
than 96 attempts per day. This completion-based interval avoids overlapping
scheduled runs. Enabling the timer makes it start on boot; after reboot it again
waits 15 minutes. It does not replay missed snapshots from server downtime.
See the [systemd timer reference](https://manpages.ubuntu.com/manpages/resolute/man5/systemd.timer.5.html)
for `OnActiveSec` and `OnUnitInactiveSec` semantics.

The shared PostgreSQL provider gate remains authoritative. A nearby camera or
manual request can cause a traffic attempt to fail its cooldown check; the next
scheduled attempt waits another 15 minutes rather than retrying immediately.
If the configured provider cooldown exceeds 900 seconds, increase the timer
interval to match; do not lower the existing cooldown to force a run through.

After the first due run, check both the service and the data (an active timer alone
does not prove successful or fresh ingestion):

```bash
sudo journalctl -u alternative-data-traffic.service -n 50 --no-pager
systemctl show alternative-data-traffic.service -p Result -p ExecMainStatus -p ExecMainExitTimestamp

docker compose --env-file .env -f docker-compose.server.yml exec -T db \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
BEGIN READ ONLY;
SELECT r.id, r.status, r.started_at, r.finished_at,
       r.rows_received, r.rows_inserted, r.rows_skipped
FROM ingestion_runs r
JOIN data_sources s ON s.id = r.source_id
WHERE s.name = 'travel_midwest_link_traffic'
ORDER BY r.id DESC LIMIT 5;

SELECT COUNT(*) AS traffic_rows,
       MAX(observed_at) AS newest_observation,
       MAX(retrieved_at) AS latest_saved_retrieval
FROM traffic_observations;

SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;
COMMIT;
SQL
```

Check freshness, failed attempts, and database growth after the first day and week.
At 469 new observations per attempt this is approximately 45,000 rows per day;
duplicates, failures, runtime, and provider coverage change that estimate. Raw
payload truncation, automatic retention, alerts, and off-host backup automation
remain unresolved; enabling this timer does not fix them or backfill missing dates.
The service follows the existing jobs' execution model: an indefinitely stuck run
blocks its next scheduled run and needs operator investigation.

To stop future traffic launches without affecting other jobs or deleting data:

```bash
sudo systemctl disable --now alternative-data-traffic.timer
```

Stopping the timer does not cancel a service run already in progress; let that run
finish before maintenance.

## Operations

```bash
cd /opt/traffic-and-market-data-model
docker compose --env-file .env -f docker-compose.server.yml ps
sudo journalctl -u alternative-data-camera.service -n 100 --no-pager
sudo journalctl -u alternative-data-market.service -n 100 --no-pager
sudo journalctl -u alternative-data-traffic.service -n 100 --no-pager
```

Run the read-only health check after deployment or a reboot. It does not call external providers:

```bash
bash deploy/check-server-health.sh
```

Create a local, timestamped PostgreSQL backup before maintenance or a reboot. Backups remain in the server-only `backups/` directory, which Git ignores:

```bash
bash deploy/backup-postgres.sh
```

The backup script never deletes or restores database data. Restore procedures are intentionally manual because restoring overwrites a database.

PostgreSQL listens only on `127.0.0.1`, not the public network. For DBeaver, use an SSH tunnel to the server rather than opening port 5432 in the firewall.
