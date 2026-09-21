from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from scripts import ingest_yahoo_finance as batch
from src.alt_data.ingestion.market.yahoo_finance import YahooFinanceClient, YahooFinanceMarketIngestor, _as_observed_at
from tests.test_yahoo_finance import PAYLOAD


@pytest.mark.parametrize("change", [
    {"high": 577}, {"low": 586}, {"close": float("nan")},
    {"adjusted_close": "Infinity"}, {"volume": -1}, {"volume": 1.5},
    {"volume": 2**63}, {"observed_at": "2025-01-02T12:00:00"},
    {"observed_at": datetime(2025, 1, 2)},
])
def test_invalid_market_values_rejected(change):
    with pytest.raises(ValueError):
        YahooFinanceMarketIngestor(None, "SPY", "2025-01-01").validate([{**PAYLOAD[0], **change}])


def test_offset_is_converted_not_relabelled():
    assert _as_observed_at("2025-01-02T08:30:00-06:00") == datetime(2025, 1, 2, 14, 30, tzinfo=timezone.utc)


def test_missing_adjusted_history_does_not_become_raw_close(monkeypatch):
    response = httpx.Response(200, request=httpx.Request("GET", "https://example.test"), json={
        "chart": {"result": [{"timestamp": [1], "indicators": {"quote": [{
            "open": [1], "high": [2], "low": [1], "close": [2], "volume": [100],
        }]}}]},
    })
    monkeypatch.setattr(httpx, "get", lambda *a, **k: response)
    with pytest.raises(ValueError, match="adjusted-close"):
        YahooFinanceClient().history("SPY", "2025-01-01")


def test_batch_continues_after_failure_and_returns_nonzero(monkeypatch):
    seen = []
    sessions = []

    class TestSession:
        def __enter__(self):
            sessions.append(self)
            return self

        def __exit__(self, *args):
            return False

    class TestIngestor:
        def __init__(self, session, symbol, **kwargs):
            self.symbol = symbol

        def run(self):
            seen.append(self.symbol)
            if self.symbol == "SPY":
                raise ValueError("fixture failure")
            return SimpleNamespace(id=1)

    monkeypatch.setattr(batch.settings, "market_symbols", "SPY,AMZN,spy,UPS")
    monkeypatch.setattr(batch, "get_session", TestSession)
    monkeypatch.setattr(batch, "YahooFinanceMarketIngestor", TestIngestor)
    assert batch.main() == 1
    assert seen == ["SPY", "AMZN", "UPS"]
    assert len(sessions) == 3
