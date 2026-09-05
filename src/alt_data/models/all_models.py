from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from src.alt_data.database.base import Base


class CreatedAt:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DataSource(CreatedAt, Base):
    __tablename__ = "data_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    rows_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class RawPayload(CreatedAt, Base):
    __tablename__ = "raw_payloads"
    id: Mapped[int] = mapped_column(primary_key=True)
    ingestion_run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"), nullable=False)
    request_url: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100))
    payload_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str | None] = mapped_column(Text)
    external_path: Mapped[str | None] = mapped_column(Text)


class EconomicIndicator(CreatedAt, Base):
    __tablename__ = "economic_indicators"
    __table_args__ = (UniqueConstraint("source_id", "indicator_code", "geography_code", "observed_at", name="uq_economic_observation"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    indicator_code: Mapped[str] = mapped_column(String(100), nullable=False)
    indicator_name: Mapped[str] = mapped_column(String(255), nullable=False)
    geography_code: Mapped[str] = mapped_column(String(30), nullable=False)
    geography_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CameraLocation(CreatedAt, Base):
    __tablename__ = "camera_locations"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_camera_external"),)


class TrafficCameraObservation(CreatedAt, Base):
    __tablename__ = "traffic_camera_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("camera_locations.id"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    image_path: Mapped[str | None] = mapped_column(Text)


class RoadwaySensorObservation(CreatedAt, Base):
    __tablename__ = "roadway_sensor_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    sensor_external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)


class WeatherObservation(CreatedAt, Base):
    __tablename__ = "weather_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    station_external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))


class MarketPrice(CreatedAt, Base):
    __tablename__ = "market_prices"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)


# Phase 2 transportation tables. The original placeholder camera/sensor tables
# remain for backwards compatibility; these normalized tables model provider
# metadata and time-series observations explicitly.
class TransportationSource(CreatedAt, Base):
    __tablename__ = "transportation_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    agency: Mapped[str | None] = mapped_column(String(150))
    region: Mapped[str | None] = mapped_column(String(150))
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)


class TrafficCamera(CreatedAt, Base):
    __tablename__ = "traffic_cameras"
    __table_args__ = (UniqueConstraint("source_id", "external_camera_id", name="uq_traffic_camera_source_external"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("transportation_sources.id"), nullable=False)
    external_camera_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    roadway: Mapped[str | None] = mapped_column(String(150))
    direction: Mapped[str | None] = mapped_column(String(20))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    agency: Mapped[str | None] = mapped_column(String(150))
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text)
    video_url: Mapped[str | None] = mapped_column(Text)
    warning_age: Mapped[bool | None] = mapped_column()
    too_old: Mapped[bool | None] = mapped_column()
    age_minutes: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class CameraSnapshot(CreatedAt, Base):
    __tablename__ = "camera_snapshots"
    __table_args__ = (UniqueConstraint("camera_id", "sha256_hash", name="uq_camera_snapshot_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("traffic_cameras.id"), nullable=False)
    source_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(Text)
    source_image_url: Mapped[str | None] = mapped_column(Text)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(100))
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)


class TrafficSensor(CreatedAt, Base):
    __tablename__ = "traffic_sensors"
    __table_args__ = (UniqueConstraint("source_id", "external_sensor_id", name="uq_traffic_sensor_source_external"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("transportation_sources.id"), nullable=False)
    external_sensor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    roadway: Mapped[str | None] = mapped_column(String(150))
    direction: Mapped[str | None] = mapped_column(String(20))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    sensor_type: Mapped[str | None] = mapped_column(String(100))
    agency: Mapped[str | None] = mapped_column(String(150))
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TrafficObservation(CreatedAt, Base):
    __tablename__ = "traffic_observations"
    __table_args__ = (UniqueConstraint("sensor_id", "observed_at", name="uq_traffic_observation_sensor_time"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("traffic_sensors.id"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    speed_mph: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    occupancy: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    travel_time_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    status: Mapped[str | None] = mapped_column(String(50))


class TransportationIncident(CreatedAt, Base):
    __tablename__ = "transportation_incidents"
    __table_args__ = (UniqueConstraint("source_id", "external_incident_id", name="uq_transport_incident_source_external"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("transportation_sources.id"), nullable=False)
    external_incident_id: Mapped[str] = mapped_column(String(200), nullable=False)
    incident_type: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    roadway: Mapped[str | None] = mapped_column(String(150))
    direction: Mapped[str | None] = mapped_column(String(20))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str | None] = mapped_column(String(50))


class ConstructionEvent(CreatedAt, Base):
    __tablename__ = "construction_events"
    __table_args__ = (UniqueConstraint("source_id", "external_event_id", name="uq_construction_source_external"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("transportation_sources.id"), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    roadway: Mapped[str | None] = mapped_column(String(150))
    direction: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lanes_closed: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(50))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
