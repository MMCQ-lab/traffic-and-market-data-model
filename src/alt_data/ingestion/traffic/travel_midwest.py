from __future__ import annotations

import logging
import csv
import io
import hashlib
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from src.alt_data.config.settings import settings

logger = logging.getLogger(__name__)


class TravelMidwestClient:
    """Authenticated, rate-limited client for registered Travel Midwest feeds."""

    _last_request: dict[str, float] = {}
    _lock = threading.Lock()

    def __init__(self, username: str | None = None, password: str | None = None, min_interval: int | None = None) -> None:
        self.username = username or settings.travel_midwest_username
        self.password = password or settings.travel_midwest_password
        self.min_interval = min_interval if min_interval is not None else settings.travel_midwest_min_interval_seconds

    def fetch_feed(self, url: str) -> tuple[int, str, bytes, str | None]:
        if not url:
            raise ValueError("Travel Midwest feed URL is not configured")
        with self._lock:
            elapsed = time.monotonic() - self._last_request.get(url, 0.0)
            if elapsed < self.min_interval:
                raise RuntimeError(f"Travel Midwest rate limit: retry in {self.min_interval - elapsed:.0f}s")
            self._last_request[url] = time.monotonic()
        headers = {"User-Agent": "alternative-data-platform/0.1 (registered research client)"}
        with httpx.Client(timeout=30.0, headers=headers) as client:
            if self.username and self.password:
                login = client.post("https://travelmidwest.com/lmiga/user/login.json", json={"username": self.username, "password": self.password})
                login.raise_for_status()
            # Travel Midwest permits an XML/CSV feed request no more than once
            # every five minutes. A transport retry may still reach the source,
            # so record the failure and let the scheduler make the next attempt.
            response = client.get(url)
            response.raise_for_status()
            logger.info("Travel Midwest request succeeded: status=%s url=%s", response.status_code, url)
            return response.status_code, response.text, response.content, response.headers.get("content-type")


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
