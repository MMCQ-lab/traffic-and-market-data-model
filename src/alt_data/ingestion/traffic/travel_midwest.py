from __future__ import annotations

import logging
import csv
import gzip
import io
import hashlib
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlsplit

import httpx

from src.alt_data.config.settings import settings
from src.alt_data.ingestion.request_gate import PostgresRequestGate

logger = logging.getLogger(__name__)


class TravelMidwestClient:
    """Authenticated, rate-limited client for registered Travel Midwest feeds."""

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        min_interval: int | None = None,
        read_timeout_seconds: float | None = None,
        gate=None,
    ) -> None:
        self.username = username or settings.travel_midwest_username
        self.password = password or settings.travel_midwest_password
        self.min_interval = min_interval if min_interval is not None else settings.travel_midwest_min_interval_seconds
        if self.min_interval < 300:
            raise ValueError("Travel Midwest request interval must be at least 300 seconds")
        self.read_timeout_seconds = (
            read_timeout_seconds
            if read_timeout_seconds is not None
            else settings.travel_midwest_read_timeout_seconds
        )
        if self.read_timeout_seconds <= 0:
            raise ValueError("Travel Midwest read timeout must be positive")
        self.gate = gate if gate is not None else PostgresRequestGate()

    def fetch_feed(self, url: str, *, basic_auth: bool = False) -> tuple[int, str, bytes, str | None]:
        if not url:
            raise ValueError("Travel Midwest feed URL is not configured")
        if basic_auth and (not self.username or not self.password):
            raise ValueError("Travel Midwest download credentials are required")
        if basic_auth:
            parsed_url = urlsplit(url)
            if parsed_url.scheme != "https" or parsed_url.hostname != "travelmidwest.com":
                raise ValueError("Refusing to send Travel Midwest credentials to an untrusted URL")
        headers = {"User-Agent": "alternative-data-platform/0.1 (registered research client)"}
        auth = httpx.BasicAuth(self.username, self.password) if basic_auth else None
        timeout = httpx.Timeout(connect=10.0, read=self.read_timeout_seconds, write=30.0, pool=10.0)
        with httpx.Client(timeout=timeout, headers=headers, auth=auth) as client:
            # Travel Midwest permits an XML/CSV feed request no more than once
            # every five minutes. A transport retry may still reach the source,
            # so record the failure and let the scheduler make the next attempt.
            # A conservative provider-wide gate avoids URL aliases bypassing the
            # interval. Claim immediately before feed I/O, after authentication.
            with self.gate.acquire("travel_midwest_feeds", self.min_interval):
                response = client.get(url)
                response.raise_for_status()
            logger.info("Travel Midwest request succeeded: status=%s url=%s", response.status_code, url)
            raw = response.content
            if raw.startswith(b"\x1f\x8b"):
                raw = gzip.decompress(raw)
            return response.status_code, raw.decode("utf-8"), raw, response.headers.get("content-type")


def _value(element: ET.Element, *names: str) -> str | None:
    for name in names:
        if element.get(name) is not None:
            return element.get(name)
        child = next((c for c in element if c.tag.rsplit("}", 1)[-1].lower() == name.lower()), None)
        if child is not None and child.text:
            return child.text.strip()
    return None


def parse_camera_feed(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Parse the documented camera-report fields without assuming one XML namespace."""
    root = ET.fromstring(xml_bytes)
    rows: list[dict[str, Any]] = []
    for element in root.iter():
        camera_id = _value(element, "CameraID", "camera_id", "id", "Id")
        location = _value(element, "CameraLocation", "location", "name")
        snapshot = _value(element, "SnapShot", "Snapshot", "image_url", "ImageURL")
        if not camera_id or (not location and not snapshot):
            continue
        rows.append({
            "external_camera_id": camera_id,
            "name": location,
            "roadway": _value(element, "Roadway", "road", "Route"),
            "direction": _value(element, "CameraDirection", "direction"),
            "latitude": _value(element, "y", "Latitude", "latitude"),
            "longitude": _value(element, "x", "Longitude", "longitude"),
            "agency": _value(element, "Agency", "agency"),
            "image_url": snapshot,
            "video_url": _value(element, "VideoUrl", "video_url"),
            "warning_age": _value(element, "WarningAge", "warning_age"),
            "too_old": _value(element, "TooOld", "too_old"),
            "age_minutes": _value(element, "AgeInMinutes", "age_minutes"),
        })
    return rows


def parse_camera_csv(csv_bytes: bytes) -> list[dict[str, Any]]:
    """Parse the camera-report CSV export (field names vary by report version)."""
    text = csv_bytes.decode("utf-8-sig", errors="strict")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    for item in reader:
        normalized = {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in item.items() if k}
        def get(*names: str) -> str | None:
            for name in names:
                value = normalized.get(name.lower())
                if value:
                    return value
            return None
        camera_id = get("cameraid", "camera_id", "id", "camid", "external_camera_id")
        location = get("cameralocation", "location", "name")
        snapshot = get("snapshot", "imageurl", "image_url", "snapshoturl")
        direction = get("cameradirection", "direction") or "NONE"
        latitude = get("y", "latitude", "lat")
        longitude = get("x", "longitude", "lon", "lng")
        if not camera_id and (location or snapshot):
            # cameraInfo.csv intentionally has no ID column. ImgPath is
            # deprecated, so derive a stable view key from documented fields.
            identity = "|".join((location or "", latitude or "", longitude or "", direction))
            camera_id = "derived-" + hashlib.sha1(identity.encode("utf-8")).hexdigest()
        if not camera_id or (not location and not snapshot):
            continue
        rows.append({
            "external_camera_id": camera_id, "name": location,
            "roadway": get("roadway", "road", "route"),
            "direction": direction,
            "latitude": latitude, "longitude": longitude,
            "agency": get("agency"), "image_url": snapshot,
            "video_url": get("videourl", "video_url"), "warning_age": get("warningage", "warning_age"),
            "too_old": get("tooold", "too_old"), "age_minutes": get("ageinminutes", "age_minutes"),
        })
    return rows
