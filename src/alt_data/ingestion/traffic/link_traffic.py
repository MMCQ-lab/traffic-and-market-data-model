"""Travel Midwest link-traffic observations.

The provider publishes speed in metres/second, volume in vehicles/lane/hour,
occupancy as a percentage, travel time in seconds, and timestamps as Unix
milliseconds. Normalized storage uses mph and timezone-aware UTC timestamps.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import logging
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from src.alt_data.config.settings import settings
from src.alt_data.ingestion.base import BaseIngestor
from src.alt_data.ingestion.traffic.travel_midwest import TravelMidwestClient
from src.alt_data.models import DataSource, TrafficObservation, TrafficSensor, TransportationSource

logger = logging.getLogger(__name__)

MPS_TO_MPH = Decimal("2.2369362920544")
LINK_TRAFFIC_PATH = "/lmiga/LinkTrafficReport.xml.gz"
INVALID_LOCATION_STATUSES = {
    "LOCATION_NOT_VALIDATED",
    "LOCATION_UNRESOLVABLE_AUTO",
    "LOCATION_UNRESOLVABLE_MANUAL",
}
INVALID_DATA_STATUSES = {
    "FIELD_DATA_NOT_VALIDATED",
    "FIELD_DATA_INFEASIBLE_FOUND_AUTO",
    "FIELD_DATA_INFEASIBLE_FOUND_MANUAL",
}


def _name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _direct(element: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in element if _name(child) == name), None)


def _text(element: ET.Element | None, name: str) -> str | None:
    if element is None:
        return None
    child = _direct(element, name)
    return child.text.strip() if child is not None and child.text else None


def parse_link_traffic_feed(xml_bytes: bytes) -> list[dict[str, str | None]]:
    """Parse documented LinkTrafficReport output without assuming a namespace."""
    root = ET.fromstring(xml_bytes)
    rows: list[dict[str, str | None]] = []
    for element in root.iter():
        if not _name(element).endswith("LinkTrafficReportElement"):
            continue
        rows.append({
            "external_sensor_id": _text(element, "linkID"),
            "roadway": _text(element, "linkDesc"),
            "observed_at_ms": _text(element, "timeStamp"),
            "travel_time_seconds": _text(element, "travelTime"),
            "volume": _text(element, "volume"),
            "speed_mps": _text(element, "speed"),
            "occupancy": _text(element, "occupancy"),
            "congestion_level": _text(element, "congestionLevel"),
            "location_status": _text(element, "locStatus"),
            "data_status": _text(element, "dataStatus"),
        })
    return rows


def _decimal(value: str | None, field: str) -> Decimal:
    if value is None or value == "":
        raise ValueError(f"Link traffic {field} is missing")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Link traffic {field} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"Link traffic {field} must be finite")
    return result


def _observed_at(value: str | None) -> datetime:
    milliseconds = _decimal(value, "timestamp")
    if milliseconds <= 0 or milliseconds != milliseconds.to_integral_value():
        raise ValueError("Link traffic timestamp must be a positive integer")
    try:
        return datetime.fromtimestamp(int(milliseconds) / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("Link traffic timestamp is out of range") from exc


def validate_link_traffic_url(url: str) -> None:
    """Fail before network I/O if another Travel Midwest feed is configured."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "travelmidwest.com"
        or parsed.path != LINK_TRAFFIC_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "TRAVEL_MIDWEST_TRAFFIC_FEED_URL must be "
            "https://travelmidwest.com/lmiga/LinkTrafficReport.xml.gz"
        )


class TravelMidwestLinkTrafficIngestor(BaseIngestor):
    source_name = "travel_midwest_link_traffic"
    source_url = "https://travelmidwest.com"

    def __init__(self, session, client: TravelMidwestClient | None = None) -> None:
        super().__init__(session)
        self.client = client or TravelMidwestClient()

    def fetch(self) -> tuple[str, int, str | None, bytes, Any]:
        url = settings.travel_midwest_traffic_feed_url
        if not url:
            raise ValueError("TRAVEL_MIDWEST_TRAFFIC_FEED_URL is required")
        validate_link_traffic_url(url)
        status, _text_body, raw, content_type = self.client.fetch_feed(url, basic_auth=True)
        return url, status, content_type, raw, raw

    def validate(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, (bytes, bytearray)):
            raise ValueError("Expected XML bytes from Travel Midwest link traffic feed")
        try:
            parsed = parse_link_traffic_feed(bytes(payload))
        except ET.ParseError as exc:
            raise ValueError("Travel Midwest returned invalid link traffic XML") from exc
        if not parsed:
            raise ValueError("Travel Midwest returned no link traffic records")

        rows: list[dict[str, Any]] = []
        rejected: Counter[str] = Counter()
        seen_observations: set[tuple[str, datetime]] = set()
        for item in parsed:
            external_sensor_id = item["external_sensor_id"]
            if not external_sensor_id:
                rejected["missing_link_id"] += 1
                continue
            if len(external_sensor_id) > 200:
                rejected["link_id_too_long"] += 1
                continue
            if item["location_status"] in INVALID_LOCATION_STATUSES:
                rejected["invalid_location"] += 1
                continue
            if item["data_status"] in INVALID_DATA_STATUSES:
                rejected["invalid_data"] += 1
                continue
            if item["congestion_level"] == "UNKNOWN_CONGESTION_LEVEL":
                rejected["unknown_congestion"] += 1
                continue
            try:
                speed_mps = _decimal(item["speed_mps"], "speed")
                volume = _decimal(item["volume"], "volume")
                occupancy = _decimal(item["occupancy"], "occupancy")
                travel_time = _decimal(item["travel_time_seconds"], "travel time")
                observed_at = _observed_at(item["observed_at_ms"])
            except ValueError:
                rejected["invalid_measurement"] += 1
                continue
            if speed_mps < 0 or volume < 0 or not 0 <= occupancy <= 100 or travel_time < 0:
                rejected["out_of_range"] += 1
                continue
            roadway = item["roadway"]
            if roadway and len(roadway) > 150:
                rejected["roadway_too_long"] += 1
                continue
            observation_key = (external_sensor_id, observed_at)
            if observation_key in seen_observations:
                rejected["duplicate_observation"] += 1
                continue
            seen_observations.add(observation_key)
            rows.append({
                "external_sensor_id": external_sensor_id,
                "roadway": roadway,
                "observed_at": observed_at,
                "speed_mph": speed_mps * MPS_TO_MPH,
                "volume": volume,
                "occupancy": occupancy,
                "travel_time_seconds": travel_time,
                "status": item["congestion_level"],
            })
        if rejected:
            logger.warning("Rejected link traffic records: counts=%s", dict(sorted(rejected.items())))
        if not rows:
            raise ValueError("Travel Midwest returned no valid link traffic observations")
        return rows

    def save(self, source: DataSource, rows: list[dict[str, Any]], retrieved_at: datetime) -> tuple[int, int]:
        transport_source = self.session.scalar(select(TransportationSource).where(
            TransportationSource.name == self.source_name
        ))
        if transport_source is None:
            transport_source = TransportationSource(
                name=self.source_name,
                agency="IDOT Gateway / Travel Midwest",
                region="Chicago area",
                source_type="link_traffic",
                source_url=settings.travel_midwest_traffic_feed_url or self.source_url,
            )
            self.session.add(transport_source)
            self.session.flush()

        inserted = skipped = 0
        for row in rows:
            sensor = self.session.scalar(select(TrafficSensor).where(
                TrafficSensor.source_id == transport_source.id,
                TrafficSensor.external_sensor_id == row["external_sensor_id"],
            ))
            if sensor is None:
                sensor = TrafficSensor(
                    source_id=transport_source.id,
                    external_sensor_id=row["external_sensor_id"],
                    roadway=row["roadway"],
                    sensor_type="LINK_TRAFFIC",
                    agency="IDOT Gateway / Travel Midwest",
                    active=True,
                    first_seen_at=retrieved_at,
                    last_seen_at=retrieved_at,
                )
                self.session.add(sensor)
                self.session.flush()
            else:
                sensor.roadway = row["roadway"] or sensor.roadway
                sensor.active = True
                sensor.last_seen_at = retrieved_at

            exists = self.session.scalar(select(TrafficObservation.id).where(
                TrafficObservation.sensor_id == sensor.id,
                TrafficObservation.observed_at == row["observed_at"],
            ))
            if exists:
                skipped += 1
                continue
            self.session.add(TrafficObservation(
                sensor_id=sensor.id,
                observed_at=row["observed_at"],
                published_at=None,
                retrieved_at=retrieved_at,
                speed_mph=row["speed_mph"],
                volume=row["volume"],
                occupancy=row["occupancy"],
                travel_time_seconds=row["travel_time_seconds"],
                status=row["status"],
            ))
            inserted += 1
        return inserted, skipped
