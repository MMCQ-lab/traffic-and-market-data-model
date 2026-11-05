"""National Weather Service station observations across Chicagoland.

Each run asks api.weather.gov for the latest observation at every configured
station. Stations report about hourly (with extra reports in changing weather),
so a 15-minute schedule mostly sees repeats; those are skipped by the
(source, station, observed_at) unique key. Values are stored in metric units as
reported, with the station's observation time and our retrieval time kept apart
for point-in-time-correct joins.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy import select

from src.alt_data.config.settings import settings
from src.alt_data.ingestion.base import BaseIngestor
from src.alt_data.models import DataSource, WeatherObservation

logger = logging.getLogger(__name__)

API_BASE = "https://api.weather.gov"
STATION_PATTERN = re.compile(r"^[A-Z0-9]{3,5}$")
STATION_NAMES = {
    "KORD": "Chicago O'Hare",
    "KMDW": "Chicago Midway",
    "KDPA": "DuPage Airport (West Chicago)",
    "KLOT": "Lewis University (Romeoville)",
    "KPWK": "Chicago Executive (Wheeling)",
    "KARR": "Aurora Municipal",
    "KGYY": "Gary/Chicago Airport",
    "KUGN": "Waukegan National",
}
# Unit codes the NWS API uses for these fields, mapped to our stored units.
UNIT_CONVERSIONS = {
    "temperature": {"wmoUnit:degC": lambda v: v, "wmoUnit:degF": lambda v: (v - 32) * Decimal(5) / Decimal(9)},
    "wind": {"wmoUnit:km_h-1": lambda v: v, "wmoUnit:m_s-1": lambda v: v * Decimal("3.6")},
    "length_mm": {"wmoUnit:mm": lambda v: v, "wmoUnit:m": lambda v: v * 1000},
    "percent": {"wmoUnit:percent": lambda v: v},
    "degrees": {"wmoUnit:degree_(angle)": lambda v: v},
}
MAX_FUTURE_SKEW = timedelta(minutes=10)
MAX_AGE = timedelta(hours=6)


def parse_stations(value: str) -> list[str]:
    stations = []
    for item in value.split(","):
        station = item.strip().upper()
        if not station:
            continue
        if not STATION_PATTERN.match(station):
            raise ValueError(f"Invalid weather station identifier: {station!r}")
        if station not in stations:
            stations.append(station)
    if not stations:
        raise ValueError("WEATHER_STATIONS must list at least one station")
    return stations


def _measure(props: dict, field: str, kind: str, low: Decimal, high: Decimal) -> Decimal | None:
    """A quantity in our units, or None when missing, unknown-unit, or implausible."""
    item = props.get(field)
    if not isinstance(item, dict) or item.get("value") is None:
        return None
    convert = UNIT_CONVERSIONS[kind].get(item.get("unitCode"))
    if convert is None:
        return None
    try:
        value = convert(Decimal(str(item["value"])))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or not low <= value <= high:
        return None
    return value.quantize(Decimal("0.001"))


class NwsWeatherIngestor(BaseIngestor):
    source_name = "nws_station_observations"
    source_url = API_BASE

    def __init__(self, session, client: httpx.Client | None = None, stations: list[str] | None = None) -> None:
        super().__init__(session)
        self.client = client
        self.stations = stations or parse_stations(settings.weather_stations)

    def fetch(self) -> tuple[str, int, str | None, bytes, Any]:
        headers = {"User-Agent": settings.weather_user_agent, "Accept": "application/geo+json"}
        timeout = httpx.Timeout(connect=10.0, read=settings.weather_read_timeout_seconds, write=10.0, pool=10.0)
        client = self.client or httpx.Client(timeout=timeout, headers=headers, follow_redirects=False)
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        try:
            for station in self.stations:
                url = f"{API_BASE}/stations/{station}/observations/latest"
                try:
                    response = client.get(url, headers=headers)
                except httpx.HTTPError as exc:
                    errors[station] = type(exc).__name__
                    continue
                if response.status_code != 200:
                    errors[station] = f"HTTP {response.status_code}"
                    continue
                try:
                    results[station] = response.json()
                except ValueError:
                    errors[station] = "invalid JSON"
        finally:
            if self.client is None:
                client.close()
        if errors:
            logger.warning("Weather stations without data this run: %s", errors)
        if not results:
            raise RuntimeError(f"No weather station returned data ({len(errors)} failed)")
        payload = {"stations": results, "errors": errors}
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return f"{API_BASE}/stations/{{station}}/observations/latest", 200, "application/geo+json", raw, payload

    def validate(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or not isinstance(payload.get("stations"), dict):
            raise ValueError("Expected a station-to-observation mapping")
        now = datetime.now(timezone.utc)
        rows: list[dict[str, Any]] = []
        rejected: Counter[str] = Counter()
        for station, document in payload["stations"].items():
            props = document.get("properties") if isinstance(document, dict) else None
            if not isinstance(props, dict):
                rejected["missing_properties"] += 1
                continue
            try:
                observed_at = datetime.fromisoformat(str(props.get("timestamp")))
            except ValueError:
                rejected["invalid_timestamp"] += 1
                continue
            if observed_at.tzinfo is None:
                rejected["naive_timestamp"] += 1
                continue
            observed_at = observed_at.astimezone(timezone.utc)
            if observed_at > now + MAX_FUTURE_SKEW or observed_at < now - MAX_AGE:
                rejected["timestamp_out_of_window"] += 1
                continue
            row = {
                "station_external_id": station,
                "station_name": STATION_NAMES.get(station) or (props.get("stationName") or None),
                "observed_at": observed_at,
                "temperature_c": _measure(props, "temperature", "temperature", Decimal(-60), Decimal(60)),
                "dewpoint_c": _measure(props, "dewpoint", "temperature", Decimal(-70), Decimal(40)),
                "relative_humidity_pct": _measure(props, "relativeHumidity", "percent", Decimal(0), Decimal(100)),
                "wind_speed_kmh": _measure(props, "windSpeed", "wind", Decimal(0), Decimal(300)),
                "wind_direction_deg": _measure(props, "windDirection", "degrees", Decimal(0), Decimal(360)),
                "precipitation_last_hour_mm": _measure(props, "precipitationLastHour", "length_mm", Decimal(0), Decimal(300)),
                "conditions": (str(props.get("textDescription")).strip()[:150] or None) if props.get("textDescription") else None,
            }
            measured = ("temperature_c", "dewpoint_c", "relative_humidity_pct", "wind_speed_kmh", "precipitation_last_hour_mm")
            if all(row[key] is None for key in measured):
                rejected["no_measurements"] += 1
                continue
            rows.append(row)
        if rejected:
            logger.warning("Rejected weather observations: counts=%s", dict(sorted(rejected.items())))
        if not rows:
            raise ValueError("No valid weather observations in this run")
        return rows

    def save(self, source: DataSource, rows: list[dict[str, Any]], retrieved_at: datetime) -> tuple[int, int]:
        inserted = skipped = 0
        for row in rows:
            exists = self.session.scalar(select(WeatherObservation.id).where(
                WeatherObservation.source_id == source.id,
                WeatherObservation.station_external_id == row["station_external_id"],
                WeatherObservation.observed_at == row["observed_at"],
            ))
            if exists:
                skipped += 1
                continue
            self.session.add(WeatherObservation(source_id=source.id, retrieved_at=retrieved_at, **row))
            inserted += 1
        return inserted, skipped
