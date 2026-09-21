from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.alt_data.database.base import Base
from src.alt_data.ingestion.base import BaseIngestor
from src.alt_data.models import EconomicIndicator, IngestionRun, RawPayload


class FixtureIngestor(BaseIngestor):
    source_name = "fixture"
    source_url = "https://example.test"

    def __init__(self, session, fail_at=None):
        super().__init__(session)
        self.fail_at = fail_at

    def fetch(self):
        if self.fail_at == "fetch":
            raise ValueError("fetch failed")
        return self.source_url, 200, "application/json", b'{"fixture":true}', [{}]

    def validate(self, payload):
        if self.fail_at == "validate":
            raise ValueError("validate failed")
        return payload

    def save(self, source, rows, retrieved_at):
        self.session.add(EconomicIndicator(
            source_id=source.id, indicator_code="TEST", indicator_name="Test",
            geography_code="USA", geography_name="United States", value=Decimal("1"),
            observed_at=datetime(2025, 1, 1, tzinfo=timezone.utc), retrieved_at=retrieved_at,
        ))
        self.session.flush()
        if self.fail_at == "save":
            raise ValueError("save failed")
        return 1, 0


def assert_lifecycle(engine, fail_at):
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        ingestor = FixtureIngestor(session, fail_at)
        if fail_at:
            with pytest.raises(ValueError, match=f"{fail_at} failed"):
                ingestor.run()
        else:
            ingestor.run()
    # Verify committed behavior through a fresh session, not the identity map.
    with Session(engine) as session:
        run = session.scalars(select(IngestionRun)).one()
        assert run.status == ("failed" if fail_at else "success")
        assert run.finished_at is not None
        assert run.rows_inserted == (0 if fail_at else 1)
        assert session.scalar(select(func.count()).select_from(EconomicIndicator)) == (0 if fail_at else 1)
        payloads = session.scalars(select(RawPayload)).all()
        assert len(payloads) == (0 if fail_at == "fetch" else 1)
        if payloads:
            assert payloads[0].payload == '{"fixture":true}'


@pytest.mark.parametrize("fail_at", [None, "fetch", "validate", "save"])
def test_lifecycle_commits_evidence_but_rolls_back_failed_observations(fail_at):
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        assert_lifecycle(engine, fail_at)
    finally:
        engine.dispose()


@pytest.mark.parametrize("fail_at", [None, "fetch", "validate", "save"])
def test_lifecycle_on_migrated_postgres(postgres_database, fail_at):
    result = postgres_database.upgrade()
    assert result.returncode == 0, result.stderr
    engine = create_engine(postgres_database.url)
    try:
        assert_lifecycle(engine, fail_at)
    finally:
        engine.dispose()
