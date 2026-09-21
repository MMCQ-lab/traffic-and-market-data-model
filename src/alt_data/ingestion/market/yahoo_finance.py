from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import time
import httpx
from typing import Any

from sqlalchemy import select

from src.alt_data.ingestion.base import BaseIngestor
from src.alt_data.models import DataSource, MarketPrice

logger = logging.getLogger(__name__)


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
        arrays = [quote.get(field) for field in ("open", "high", "low", "close", "volume")]
        if any(not isinstance(values, list) or len(values) != len(timestamps) for values in arrays):
            raise ValueError("Yahoo Finance returned mismatched OHLCV arrays")
        if not isinstance(adjusted, list) or len(adjusted) != len(timestamps):
            raise ValueError("Yahoo Finance returned missing or mismatched adjusted-close data")
        records = []
        for i, timestamp in enumerate(timestamps):
            adjusted_close = adjusted[i]
            # Keep incomplete records for validation to count/report. Never
            # silently substitute unadjusted prices for adjusted history.
            records.append({
                "observed_at": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
                "open": quote["open"][i], "high": quote["high"][i], "low": quote["low"][i],
                "close": quote["close"][i], "adjusted_close": adjusted_close,
                "volume": quote["volume"][i],
            })
        return records


def _as_decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Market value must be numeric") from exc
    if not result.is_finite():
        raise ValueError("Market value must be finite")
    return result


def _as_observed_at(index_value: Any) -> datetime:
    if hasattr(index_value, "to_pydatetime"):
        index_value = index_value.to_pydatetime()
    result = index_value if isinstance(index_value, datetime) else datetime.fromisoformat(str(index_value))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Market timestamp must include a timezone")
    return result.astimezone(timezone.utc)


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
                    "adjusted_close": float(row["Adj Close"]),
                    "volume": row["Volume"],
                })
        raw = json.dumps(records, separators=(",", ":")).encode()
        url = f"{self.source_url}/quote/{self.symbol}/history"
        return url, 200, "application/json", raw, records

    def validate(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, list) or not payload:
            raise ValueError("Unexpected Yahoo Finance response shape")
        rows = []
        incomplete = 0
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("Market observation must be an object")
            fields = ("open", "high", "low", "close", "adjusted_close", "volume")
            if any(item.get(field) is None for field in fields):
                incomplete += 1
                continue
            volume = _as_decimal(item["volume"])
            if volume < 0 or volume != volume.to_integral_value() or volume > 9223372036854775807:
                raise ValueError("Market volume must be a nonnegative BIGINT")
            row = {
                "symbol": self.symbol,
                "observed_at": _as_observed_at(item["observed_at"]),
                "open": _as_decimal(item["open"]), "high": _as_decimal(item["high"]),
                "low": _as_decimal(item["low"]), "close": _as_decimal(item["close"]),
                "adjusted_close": _as_decimal(item["adjusted_close"]), "volume": int(volume),
            }
            if not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
                raise ValueError("Market OHLC values are inconsistent")
            rows.append(row)
        if incomplete:
            logger.warning("Incomplete market bars: symbol=%s rejected=%s", self.symbol, incomplete)
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
