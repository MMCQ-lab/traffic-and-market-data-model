from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import time
import httpx
from typing import Any

from sqlalchemy import select

from src.alt_data.ingestion.base import BaseIngestor
from src.alt_data.models import DataSource, MarketPrice


class YahooFinanceClient:
    """Small provider adapter; the rest of the application never sees Yahoo JSON."""

    def history(self, symbol: str, start: str, end: str | None = None) -> Any:
        start_ts = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp()) if end else int(time.time())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        response = httpx.get(url, params={"period1": start_ts, "period2": end_ts, "interval": "1d", "events": "div,splits"}, headers={"User-Agent": "alternative-data-platform/0.3"}, timeout=30.0)
        response.raise_for_status()
        result = response.json()["chart"]["result"]
        if not result:
            return []
        result = result[0]
        timestamps = result.get("timestamp", [])
        quote = result["indicators"]["quote"][0]
        adjusted = result["indicators"].get("adjclose", [{"adjclose": []}])[0].get("adjclose", [])
        records = []
        for i, timestamp in enumerate(timestamps):
            adjusted_close = adjusted[i] if i < len(adjusted) else quote["close"][i]
            values = (
                quote["open"][i], quote["high"][i], quote["low"][i],
                quote["close"][i], adjusted_close, quote["volume"][i],
            )
            # Yahoo occasionally includes an incomplete daily bar. It cannot
            # represent a complete OHLCV observation, so exclude it rather
            # than failing an otherwise valid scheduled ingestion.
            if any(value is None for value in values):
                continue
            records.append({
                "observed_at": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
                "open": quote["open"][i], "high": quote["high"][i], "low": quote["low"][i],
                "close": quote["close"][i], "adjusted_close": adjusted_close,
                "volume": quote["volume"][i],
            })
        return records


def _as_decimal(value: Any) -> Decimal:
    if value is None:
        raise ValueError("Market price contains a null OHLC value")
    return Decimal(str(value))


def _as_observed_at(index_value: Any) -> datetime:
    if hasattr(index_value, "to_pydatetime"):
        index_value = index_value.to_pydatetime()
    if isinstance(index_value, date) and not isinstance(index_value, datetime):
        return datetime(index_value.year, index_value.month, index_value.day, tzinfo=timezone.utc)
    if isinstance(index_value, datetime):
        return index_value.replace(tzinfo=timezone.utc) if index_value.tzinfo is None else index_value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(index_value)).replace(tzinfo=timezone.utc)


class YahooFinanceMarketIngestor(BaseIngestor):
    source_name = "yahoo_finance"
    source_url = "https://finance.yahoo.com"

    def __init__(self, session, symbol: str, start: str, end: str | None = None, client: YahooFinanceClient | None = None) -> None:
        super().__init__(session)
        self.symbol = symbol.upper()
        self.start = start
        self.end = end
        self.client = client or YahooFinanceClient()

    def fetch(self) -> tuple[str, int, str | None, bytes, Any]:
        frame = self.client.history(self.symbol, self.start, self.end)
        if frame is None or len(frame) == 0:
            raise ValueError(f"Yahoo Finance returned no history for {self.symbol}")
        if isinstance(frame, list):
            records = frame
        else:
            records = []
            for index_value, row in frame.iterrows():
                records.append({
                    "observed_at": _as_observed_at(index_value).isoformat(),
                    "open": float(row["Open"]), "high": float(row["High"]),
                    "low": float(row["Low"]), "close": float(row["Close"]),
                    "adjusted_close": float(row.get("Adj Close", row["Close"])),
                    "volume": int(row["Volume"]),
                })
        raw = json.dumps(records, separators=(",", ":")).encode()
        url = f"{self.source_url}/quote/{self.symbol}/history"
        return url, 200, "application/json", raw, records

    def validate(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, list) or not payload:
            raise ValueError("Unexpected Yahoo Finance response shape")
        rows = []
        for item in payload:
            fields = ("open", "high", "low", "close", "adjusted_close", "volume")
            if any(item.get(field) is None for field in fields):
                continue
            rows.append({
                "symbol": self.symbol,
                "observed_at": datetime.fromisoformat(item["observed_at"]),
                "open": _as_decimal(item["open"]), "high": _as_decimal(item["high"]),
                "low": _as_decimal(item["low"]), "close": _as_decimal(item["close"]),
                "adjusted_close": _as_decimal(item["adjusted_close"]), "volume": int(item["volume"]),
            })
        if not rows:
            raise ValueError("Yahoo Finance returned no complete daily OHLCV observations")
        return rows

    def save(self, source: DataSource, rows: list[dict[str, Any]], retrieved_at: datetime) -> tuple[int, int]:
        inserted = skipped = 0
        for row in rows:
            exists = self.session.scalar(select(MarketPrice.id).where(
                MarketPrice.source_id == source.id,
                MarketPrice.symbol == row["symbol"],
                MarketPrice.observed_at == row["observed_at"],
            ))
            if exists:
                skipped += 1
                continue
            self.session.add(MarketPrice(source_id=source.id, retrieved_at=retrieved_at, published_at=None, **row))
            inserted += 1
        return inserted, skipped
