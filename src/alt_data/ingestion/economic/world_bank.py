from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import logging
import httpx
from sqlalchemy import select
from src.alt_data.ingestion.base import BaseIngestor
from src.alt_data.models import DataSource, EconomicIndicator

logger = logging.getLogger(__name__)


class WorldBankGdpIngestor(BaseIngestor):
    source_name = "world_bank"
    source_url = "https://api.worldbank.org"
    indicator_code = "NY.GDP.MKTP.KD"
    country_code = "USA"

    def fetch(self) -> tuple[str, int, str | None, bytes, Any]:
        url = f"{self.source_url}/v2/country/{self.country_code}/indicator/{self.indicator_code}?format=json&per_page=100"
        with httpx.Client(timeout=30.0, headers={"User-Agent": "alternative-data-platform/0.1"}) as client:
            response = client.get(url)
        response.raise_for_status()
        logger.info("API request succeeded: source=%s status=%s url=%s", self.source_name, response.status_code, url)
        return url, response.status_code, response.headers.get("content-type"), response.content, response.json()

    def validate(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
            raise ValueError("Unexpected World Bank response shape")
        rows = []
        for item in payload[1]:
            if item.get("value") is None or not item.get("date", "").isdigit():
                continue
            rows.append({
                "indicator_code": item["indicator"]["id"], "indicator_name": item["indicator"]["value"],
                "geography_code": item["countryiso3code"], "geography_name": item["country"]["value"],
                "value": Decimal(str(item["value"])),
                "observed_at": datetime(int(item["date"]), 1, 1, tzinfo=timezone.utc),
            })
        return rows

    def save(self, source: DataSource, rows: list[dict[str, Any]], retrieved_at: datetime) -> tuple[int, int]:
        inserted = skipped = 0
        for row in rows:
            exists = self.session.scalar(select(EconomicIndicator.id).where(
                EconomicIndicator.source_id == source.id,
                EconomicIndicator.indicator_code == row["indicator_code"],
                EconomicIndicator.geography_code == row["geography_code"],
                EconomicIndicator.observed_at == row["observed_at"],
            ))
            if exists:
                skipped += 1
                continue
            self.session.add(EconomicIndicator(source_id=source.id, retrieved_at=retrieved_at, published_at=None, **row))
            inserted += 1
        return inserted, skipped
