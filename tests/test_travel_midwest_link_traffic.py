from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import gzip

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from src.alt_data.database.base import Base
from src.alt_data.ingestion.traffic.link_traffic import (
    MPS_TO_MPH,
    TravelMidwestLinkTrafficIngestor,
    parse_link_traffic_feed,
)
from src.alt_data.ingestion.traffic.travel_midwest import TravelMidwestClient
from src.alt_data.models import DataSource, TrafficObservation, TrafficSensor


VALID_ELEMENT = b"""
<com.gcmtravel.LinkTrafficReportElement>
  <linkID>IL-TEST-I90-001</linkID>
  <linkDesc>I-90 from Cumberland to Harlem</linkDesc>
  <travelTime>180</travelTime>
  <volume>1200</volume>
  <speed>10.0</speed>
  <occupancy>12.5</occupancy>
  <congestionLevel>LIGHT_CONGESTION</congestionLevel>
  <locStatus>LOCATION_RESOLVED_AUTO</locStatus>
  <dataStatus>FIELD_DATA_VALIDATED_AUTO</dataStatus>
  <timeStamp>1735689600000</timeStamp>
</com.gcmtravel.LinkTrafficReportElement>
"""

INVALID_ELEMENT = b"""
<com.gcmtravel.LinkTrafficReportElement>
  <linkID>IL-TEST-I90-002</linkID>
  <linkDesc>I-90 invalid fixture</linkDesc>
  <travelTime>200</travelTime>
  <volume>1000</volume>
  <speed>9.0</speed>
  <occupancy>15.0</occupancy>
  <congestionLevel>MEDIUM_CONGESTION</congestionLevel>
  <locStatus>LOCATION_RESOLVED_AUTO</locStatus>
  <dataStatus>FIELD_DATA_INFEASIBLE_FOUND_AUTO</dataStatus>
  <timeStamp>1735689600000</timeStamp>
</com.gcmtravel.LinkTrafficReportElement>
"""

FEED = b"<com.gcmtravel.LinkTrafficReport>" + VALID_ELEMENT + INVALID_ELEMENT + b"</com.gcmtravel.LinkTrafficReport>"


def test_parse_and_validate_link_traffic_uses_documented_units(caplog):
    assert len(parse_link_traffic_feed(FEED)) == 2
    with caplog.at_level("WARNING"):
        rows = TravelMidwestLinkTrafficIngestor(None).validate(FEED)

    assert len(rows) == 1
    row = rows[0]
    assert row["external_sensor_id"] == "IL-TEST-I90-001"
    assert row["observed_at"] == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert row["speed_mph"] == Decimal("10.0") * MPS_TO_MPH
    assert row["volume"] == Decimal("1200")
    assert row["occupancy"] == Decimal("12.5")
    assert row["travel_time_seconds"] == Decimal("180")
    assert row["status"] == "LIGHT_CONGESTION"
    assert "invalid_data" in caplog.text


@pytest.mark.parametrize("field,value", [
    ("speed", "-1"),
    ("volume", "-1"),
    ("occupancy", "101"),
    ("travelTime", "-1"),
    ("timeStamp", "not-a-timestamp"),
])
def test_invalid_link_measurement_does_not_become_an_observation(field, value):
    payload = VALID_ELEMENT.replace(
        f"<{field}>{'1735689600000' if field == 'timeStamp' else {'speed': '10.0', 'volume': '1200', 'occupancy': '12.5', 'travelTime': '180'}[field]}</{field}>".encode(),
        f"<{field}>{value}</{field}>".encode(),
    )
    feed = b"<com.gcmtravel.LinkTrafficReport>" + payload + b"</com.gcmtravel.LinkTrafficReport>"
    with pytest.raises(ValueError, match="no valid link traffic"):
        TravelMidwestLinkTrafficIngestor(None).validate(feed)


def test_duplicate_link_observation_is_filtered_before_database_write(caplog):
    feed = b"<com.gcmtravel.LinkTrafficReport>" + VALID_ELEMENT + VALID_ELEMENT + b"</com.gcmtravel.LinkTrafficReport>"
    with caplog.at_level("WARNING"):
        rows = TravelMidwestLinkTrafficIngestor(None).validate(feed)

    assert len(rows) == 1
    assert "duplicate_observation" in caplog.text


def test_link_traffic_save_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    source = DataSource(name="travel_midwest_link_traffic", base_url="https://travelmidwest.com")
    session.add(source)
    session.flush()
    ingestor = TravelMidwestLinkTrafficIngestor(session)
    rows = ingestor.validate(b"<com.gcmtravel.LinkTrafficReport>" + VALID_ELEMENT + b"</com.gcmtravel.LinkTrafficReport>")
    retrieved_at = datetime(2025, 1, 1, 0, 5, tzinfo=timezone.utc)

    assert ingestor.save(source, rows, retrieved_at) == (1, 0)
    session.flush()
    assert ingestor.save(source, rows, retrieved_at) == (0, 1)
    assert len(session.scalars(select(TrafficSensor)).all()) == 1
    observation = session.scalars(select(TrafficObservation)).one()
    assert observation.speed_mph == Decimal("22.369")
    # SQLite drops timezone metadata; PostgreSQL-backed lifecycle coverage
    # verifies the production timestamptz behavior.
    assert observation.retrieved_at.replace(tzinfo=timezone.utc) == retrieved_at


def test_link_traffic_save_on_migrated_postgres(postgres_database):
    result = postgres_database.upgrade()
    assert result.returncode == 0, result.stderr
    engine = create_engine(postgres_database.url)
    try:
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            source = DataSource(name="travel_midwest_link_traffic", base_url="https://travelmidwest.com")
            session.add(source)
            session.flush()
            ingestor = TravelMidwestLinkTrafficIngestor(session)
            rows = ingestor.validate(
                b"<com.gcmtravel.LinkTrafficReport>" + VALID_ELEMENT + b"</com.gcmtravel.LinkTrafficReport>"
            )
            retrieved_at = datetime(2025, 1, 1, 0, 5, tzinfo=timezone.utc)
            assert ingestor.save(source, rows, retrieved_at) == (1, 0)
            session.commit()
            assert ingestor.save(source, rows, retrieved_at) == (0, 1)
            session.commit()

        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(TrafficSensor)) == 1
            assert session.scalar(select(func.count()).select_from(TrafficObservation)) == 1
            observation = session.scalars(select(TrafficObservation)).one()
            assert observation.observed_at == datetime(2025, 1, 1, tzinfo=timezone.utc)
            assert observation.retrieved_at == retrieved_at
    finally:
        engine.dispose()


def test_download_feed_uses_basic_auth_and_decompresses_gzip(monkeypatch):
    requests = []
    client_options = []
    compressed = gzip.compress(b"<com.gcmtravel.LinkTrafficReport />")

    @contextmanager
    def allowed(*args):
        yield

    class Gate:
        acquire = staticmethod(allowed)

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=compressed, headers={"content-type": "application/gzip"})

    real_client = httpx.Client
    def mock_client(**kwargs):
        client_options.append(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", mock_client)
    client = TravelMidwestClient(username="researcher", password="secret", gate=Gate())
    status, text_body, raw, _content_type = client.fetch_feed(
        "https://travelmidwest.com/lmiga/LinkTrafficReport.xml.gz", basic_auth=True
    )

    assert status == 200
    assert raw == b"<com.gcmtravel.LinkTrafficReport />"
    assert text_body == raw.decode()
    assert requests[0].headers["authorization"].startswith("Basic ")
    assert client_options[0]["timeout"].read == 120.0


def test_read_timeout_must_be_positive():
    with pytest.raises(ValueError, match="read timeout"):
        TravelMidwestClient(read_timeout_seconds=0)


def test_basic_auth_feed_requires_credentials_before_network():
    client = TravelMidwestClient(username=None, password=None, gate=None)
    client.username = client.password = None
    with pytest.raises(ValueError, match="credentials"):
        client.fetch_feed("https://example.test/LinkTrafficReport.xml.gz", basic_auth=True)


def test_basic_auth_credentials_cannot_be_sent_to_an_untrusted_url():
    client = TravelMidwestClient(username="researcher", password="secret", gate=None)
    with pytest.raises(ValueError, match="untrusted URL"):
        client.fetch_feed("https://example.test/LinkTrafficReport.xml.gz", basic_auth=True)
