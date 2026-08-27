from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from src.alt_data.database.base import Base
from src.alt_data.ingestion.economic import WorldBankGdpIngestor
from src.alt_data.models import DataSource, EconomicIndicator


PAYLOAD = [{"page": 1}, [{
    "indicator": {"id": "NY.GDP.MKTP.KD", "value": "GDP (constant 2015 US$)"},
    "country": {"value": "United States"}, "countryiso3code": "USA", "date": "2023", "value": 22200000000000,
}]]


def test_transform_world_bank_payload() -> None:
    rows = WorldBankGdpIngestor(None).validate(PAYLOAD)
    assert rows == [{
        "indicator_code": "NY.GDP.MKTP.KD", "indicator_name": "GDP (constant 2015 US$)",
        "geography_code": "USA", "geography_name": "United States", "value": Decimal("22200000000000"),
        "observed_at": datetime(2023, 1, 1, tzinfo=timezone.utc),
    }]


def test_save_skips_duplicate_observation() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    source = DataSource(name="world_bank", base_url="https://api.worldbank.org")
    session.add(source)
    session.flush()
    ingestor = WorldBankGdpIngestor(session)
    rows = ingestor.validate(PAYLOAD)
    now = datetime.now(timezone.utc)
    assert ingestor.save(source, rows, now) == (1, 0)
    session.flush()
    assert ingestor.save(source, rows, now) == (0, 1)
    assert len(session.scalars(select(EconomicIndicator)).all()) == 1
