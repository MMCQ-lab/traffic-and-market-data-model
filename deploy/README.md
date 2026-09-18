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

The camera job runs every five minutes. The market job runs at 7:15 PM America/Chicago each weekday.

## Operations

```bash
cd /opt/traffic-and-market-data-model
docker compose --env-file .env -f docker-compose.server.yml ps
sudo journalctl -u alternative-data-camera.service -n 100 --no-pager
sudo journalctl -u alternative-data-market.service -n 100 --no-pager
```

PostgreSQL listens only on `127.0.0.1`, not the public network. For DBeaver, use an SSH tunnel to the server rather than opening port 5432 in the firewall.
