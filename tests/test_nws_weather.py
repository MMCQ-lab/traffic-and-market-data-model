from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from src.alt_data.database.base import Base
from src.alt_data.ingestion.weather.nws import NwsWeatherIngestor, parse_stations
from src.alt_data.models import DataSource, WeatherObservation


def observation(timestamp: datetime, temp=12.2, unit="wmoUnit:degC", text="Mostly Cloudy"):
    def q(value, code):
        return {"unitCode": code, "value": value, "qualityControl": "V"}
    return {"properties": {
        "stationName": "Chicago O'Hare International Airport",
        "timestamp": timestamp.isoformat(),
        "textDescription": text,
        "temperature": q(temp, unit),
        "dewpoint": q(5.6, "wmoUnit:degC"),
        "relativeHumidity": q(64.3, "wmoUnit:percent"),
        "windSpeed": q(18.36, "wmoUnit:km_h-1"),
        "windDirection": q(250, "wmoUnit:degree_(angle)"),
        "precipitationLastHour": q(None, "wmoUnit:mm"),
    }}


def recent(minutes=20):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).replace(microsecond=0)


def test_station_list_is_validated_and_deduplicated():
    assert parse_stations(" kord,KMDW,KORD ") == ["KORD", "KMDW"]
    with pytest.raises(ValueError):
        parse_stations("KORD,../etc")
    with pytest.raises(ValueError):
        parse_stations(" , ")


def test_validate_normalizes_units_and_keeps_missing_values_null():
    when = recent()
    payload = {"stations": {
        "KORD": observation(when),
        "KMDW": observation(when, temp=50.0, unit="wmoUnit:degF"),
    }, "errors": {}}
    rows = {row["station_external_id"]: row for row in NwsWeatherIngestor(None, stations=["KORD"]).validate(payload)}

    assert rows["KORD"]["temperature_c"] == Decimal("12.200")
    assert rows["KORD"]["station_name"] == "Chicago O'Hare"
    assert rows["KORD"]["wind_speed_kmh"] == Decimal("18.360")
    assert rows["KORD"]["precipitation_last_hour_mm"] is None
    assert rows["KORD"]["observed_at"] == when
    assert rows["KMDW"]["temperature_c"] == Decimal("10.000")


@pytest.mark.parametrize("change", ["future", "stale", "naive", "garbage", "no_measurements"])
def test_unusable_observations_are_rejected(change):
    doc = observation(recent())
    props = doc["properties"]
    if change == "future":
        props["timestamp"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    elif change == "stale":
        props["timestamp"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    elif change == "naive":
        props["timestamp"] = "2026-11-05T18:00:00"
    elif change == "garbage":
        props["timestamp"] = "yesterday"
    else:
        for key in ("temperature", "dewpoint", "relativeHumidity", "windSpeed", "precipitationLastHour"):
            props[key]["value"] = None
    with pytest.raises(ValueError, match="No valid weather"):
        NwsWeatherIngestor(None, stations=["KORD"]).validate({"stations": {"KORD": doc}, "errors": {}})


def test_implausible_value_is_dropped_not_stored():
    doc = observation(recent(), temp=999)
    row = NwsWeatherIngestor(None, stations=["KORD"]).validate({"stations": {"KORD": doc}, "errors": {}})[0]
    assert row["temperature_c"] is None
    assert row["dewpoint_c"] == Decimal("5.600")


def test_fetch_tolerates_one_failing_station_and_fails_when_all_fail():
    when = recent()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"]
        if "/KORD/" in request.url.path:
            return httpx.Response(200, json=observation(when))
        return httpx.Response(503)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _url, status, _ctype, raw, payload = NwsWeatherIngestor(None, client=client, stations=["KORD", "KMDW"]).fetch()
    assert status == 200 and set(payload["stations"]) == {"KORD"}
    assert payload["errors"] == {"KMDW": "HTTP 503"}
    assert b"KORD" in raw

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        with pytest.raises(RuntimeError, match="No weather station"):
            NwsWeatherIngestor(None, client=client, stations=["KORD"]).fetch()


def test_weather_save_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    source = DataSource(name="nws_station_observations", base_url="https://api.weather.gov")
    session.add(source)
    session.flush()
    ingestor = NwsWeatherIngestor(session, stations=["KORD"])
    rows = ingestor.validate({"stations": {"KORD": observation(recent())}, "errors": {}})
    retrieved_at = datetime.now(timezone.utc)

    assert ingestor.save(source, rows, retrieved_at) == (1, 0)
    session.flush()
    assert ingestor.save(source, rows, retrieved_at) == (0, 1)
    stored = session.scalars(select(WeatherObservation)).one()
    assert stored.temperature_c == Decimal("12.200") and stored.conditions == "Mostly Cloudy"


def test_weather_save_on_migrated_postgres(postgres_database):
    result = postgres_database.upgrade()
    assert result.returncode == 0, result.stderr
    engine = create_engine(postgres_database.url)
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            source = DataSource(name="nws_station_observations", base_url="https://api.weather.gov")
            session.add(source)
            session.flush()
            ingestor = NwsWeatherIngestor(session, stations=["KORD"])
            rows = ingestor.validate({"stations": {"KORD": observation(recent())}, "errors": {}})
            retrieved_at = datetime.now(timezone.utc)
            assert ingestor.save(source, rows, retrieved_at) == (1, 0)
            session.commit()
            assert ingestor.save(source, rows, retrieved_at) == (0, 1)
            stored = session.scalars(select(WeatherObservation)).one()
            assert stored.observed_at.tzinfo is not None
            assert stored.relative_humidity_pct == Decimal("64.30")
    finally:
        engine.dispose()
