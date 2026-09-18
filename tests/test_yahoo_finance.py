from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.alt_data.database.base import Base
from src.alt_data.ingestion.market import YahooFinanceMarketIngestor
from src.alt_data.models import DataSource, MarketPrice


PAYLOAD = [{
    "observed_at": "2025-01-02T00:00:00+00:00",
    "open": 580.0,
    "high": 585.0,
    "low": 578.0,
    "close": 584.0,
    "adjusted_close": 583.5,
    "volume": 123456,
}]


def test_validate_normalizes_ohlcv_payload() -> None:
    rows = YahooFinanceMarketIngestor(None, "spy", "2025-01-01").validate(PAYLOAD)
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["observed_at"] == datetime(2025, 1, 2, tzinfo=timezone.utc)
    assert rows[0]["close"] == Decimal("584.0")
    assert rows[0]["volume"] == 123456


def test_save_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    source = DataSource(name="yahoo_finance", base_url="https://finance.yahoo.com")
    session.add(source)
    session.flush()
    ingestor = YahooFinanceMarketIngestor(session, "SPY", "2025-01-01")
    rows = ingestor.validate(PAYLOAD)
    now = datetime.now(timezone.utc)
    assert ingestor.save(source, rows, now) == (1, 0)
    session.flush()
    assert ingestor.save(source, rows, now) == (0, 1)
    saved = session.scalars(select(MarketPrice)).one()
    assert saved.adjusted_close == Decimal("583.5")
