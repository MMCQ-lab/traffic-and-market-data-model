from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from src.alt_data.config.settings import settings
from src.alt_data.ingestion.base import BaseIngestor
from src.alt_data.ingestion.traffic.travel_midwest import TravelMidwestClient, parse_camera_csv, parse_camera_feed
from src.alt_data.models import DataSource, TrafficCamera, TransportationSource


def _decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid coordinate: {value}") from exc


def _boolean(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.strip().lower() == "true"


def _integer(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


class TravelMidwestCameraIngestor(BaseIngestor):
    source_name = "travel_midwest_idot_gateway"
    source_url = "https://travelmidwest.com"

    def __init__(self, session, client: TravelMidwestClient | None = None) -> None:
        super().__init__(session)
        self.client = client or TravelMidwestClient()

    def fetch(self) -> tuple[str, int, str | None, bytes, Any]:
        url = settings.travel_midwest_camera_feed_url
        if not url:
            raise ValueError("TRAVEL_MIDWEST_CAMERA_FEED_URL is required for camera ingestion")
        status, _text, raw, content_type = self.client.fetch_feed(url)
        return url, status, content_type, raw, raw

    def validate(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, (bytes, bytearray)):
            raise ValueError("Expected XML bytes from Travel Midwest camera feed")
        raw = bytes(payload).lstrip()
        if raw.startswith(b"<"):
            return parse_camera_feed(raw)
        return parse_camera_csv(raw)

    def save(self, source: DataSource, rows: list[dict[str, Any]], retrieved_at: datetime) -> tuple[int, int]:
        transport_source = self.session.scalar(select(TransportationSource).where(TransportationSource.name == self.source_name))
        if transport_source is None:
            transport_source = TransportationSource(name=self.source_name, agency="IDOT Gateway / Travel Midwest", region="Chicago area", source_type="camera_metadata", source_url=self.source_url)
            self.session.add(transport_source)
            self.session.flush()
        inserted = skipped = 0
        seen_ids: set[str] = set()
        for row in rows:
            external_id = row["external_camera_id"]
            # The public CSV can contain repeated views. Deduplicate within
            # this response before flushing so the database unique constraint
            # remains useful across runs as well as within one batch.
            if external_id in seen_ids:
                skipped += 1
                continue
            seen_ids.add(external_id)
            existing = self.session.scalar(select(TrafficCamera).where(TrafficCamera.source_id == transport_source.id, TrafficCamera.external_camera_id == row["external_camera_id"]))
            if existing:
                existing.last_seen_at = retrieved_at
                existing.active = True
                existing.image_url = row.get("image_url") or existing.image_url
                existing.video_url = row.get("video_url") or existing.video_url
                existing.warning_age = _boolean(row.get("warning_age"))
                existing.too_old = _boolean(row.get("too_old"))
                existing.age_minutes = _integer(row.get("age_minutes"))
                skipped += 1
                continue
            self.session.add(TrafficCamera(
                source_id=transport_source.id, external_camera_id=external_id, name=row.get("name"),
                roadway=row.get("roadway"), direction=row.get("direction"), latitude=_decimal(row.get("latitude")),
                longitude=_decimal(row.get("longitude")), agency=row.get("agency"), image_url=row.get("image_url"),
                video_url=row.get("video_url"), warning_age=_boolean(row.get("warning_age")),
                too_old=_boolean(row.get("too_old")), age_minutes=_integer(row.get("age_minutes")),
                first_seen_at=retrieved_at, last_seen_at=retrieved_at,
            ))
            inserted += 1
        return inserted, skipped
