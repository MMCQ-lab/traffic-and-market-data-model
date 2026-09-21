from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.alt_data.config.settings import settings
from src.alt_data.models import DataSource, IngestionRun, RawPayload

logger = logging.getLogger(__name__)


class BaseIngestor(ABC):
    """Small lifecycle contract for HTTP-backed sources."""

    source_name: str
    source_url: str

    def __init__(self, session: Session) -> None:
        self.session = session

    @abstractmethod
    def fetch(self) -> tuple[str, int, str | None, bytes, Any]: ...

    @abstractmethod
    def validate(self, payload: Any) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save(self, source: DataSource, rows: list[dict[str, Any]], retrieved_at: datetime) -> tuple[int, int]: ...

    def source(self) -> DataSource:
        source = self.session.scalar(select(DataSource).where(DataSource.name == self.source_name))
        if source is None:
            source = DataSource(name=self.source_name, base_url=self.source_url)
            self.session.add(source)
            self.session.flush()
        return source

    def run(self) -> IngestionRun:
        source = self.source()
        run = IngestionRun(source_id=source.id, started_at=datetime.now(timezone.utc), status="running")
        self.session.add(run)
        self.session.flush()
        # Persist the run before the network call so failures can be recorded
        # even when fetching raises before any payload is available.
        self.session.commit()
        run_id = run.id
        try:
            url, status, content_type, raw, payload = self.fetch()
            retrieved_at = datetime.now(timezone.utc)
            self.session.add(RawPayload(
                ingestion_run_id=run.id, request_url=url, http_status=status, content_type=content_type,
                retrieved_at=retrieved_at, payload_bytes=len(raw),
                payload=raw[:settings.raw_payload_max_bytes].decode("utf-8", errors="replace"),
            ))
            # Preserve fetched evidence even if parsing or normalized writes fail.
            # Observation writes and successful run status remain atomic below.
            self.session.commit()
            rows = self.validate(payload)
            inserted, skipped = self.save(source, rows, retrieved_at)
            run.rows_received, run.rows_inserted, run.rows_skipped = len(rows), inserted, skipped
            run.status, run.finished_at = "success", datetime.now(timezone.utc)
            self.session.commit()
            logger.info("Ingestion succeeded: source=%s run_id=%s received=%s inserted=%s skipped=%s", self.source_name, run.id, len(rows), inserted, skipped)
        except Exception as exc:
            self.session.rollback()
            run = self.session.get(IngestionRun, run_id)
            if run is None:
                raise RuntimeError("Ingestion run disappeared while recording failure") from exc
            run.status, run.error_message, run.finished_at = "failed", str(exc), datetime.now(timezone.utc)
            self.session.commit()
            logger.exception("Ingestion failed: source=%s run_id=%s", self.source_name, run.id)
            raise
        return run
