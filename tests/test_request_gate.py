from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest
from sqlalchemy import create_engine, text

from src.alt_data.ingestion.request_gate import PostgresRequestGate, ProviderRateLimited
from src.alt_data.ingestion.traffic.travel_midwest import TravelMidwestClient


@pytest.fixture
def gate_engine(postgres_database):
    result = postgres_database.upgrade()
    assert result.returncode == 0, result.stderr
    engine = create_engine(postgres_database.url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_gate_failure_retains_cooldown_and_blocks_new_instance(gate_engine):
    with pytest.raises(ValueError, match="provider failed"):
        with PostgresRequestGate(gate_engine).acquire("test", 300):
            raise ValueError("provider failed")
    with pytest.raises(ProviderRateLimited, match="cooldown"):
        with PostgresRequestGate(gate_engine).acquire("test", 300):
            pytest.fail("second request admitted")
    with gate_engine.begin() as connection:
        connection.execute(text("UPDATE provider_request_gates SET last_attempt_at = clock_timestamp() - interval '301 seconds'"))
    with PostgresRequestGate(gate_engine).acquire("test", 300):
        pass


def test_gate_blocks_overlap_even_when_timestamp_is_expired(gate_engine):
    with PostgresRequestGate(gate_engine).acquire("test", 300):
        with gate_engine.begin() as connection:
            connection.execute(text("UPDATE provider_request_gates SET last_attempt_at = clock_timestamp() - interval '301 seconds'"))
        with pytest.raises(ProviderRateLimited, match="in progress"):
            with PostgresRequestGate(gate_engine).acquire("test", 300):
                pytest.fail("overlapping request admitted")


def test_crashed_process_does_not_erase_claim(gate_engine, postgres_database):
    env = os.environ.copy()
    env["DATABASE_URL"] = postgres_database.url
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    code = """
import os
from sqlalchemy import create_engine
from src.alt_data.ingestion.request_gate import PostgresRequestGate
with PostgresRequestGate(create_engine(os.environ['DATABASE_URL'])).acquire('test', 300):
    os._exit(0)
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=postgres_database.workdir, env=env, timeout=30, capture_output=True)
    assert result.returncode == 0, result.stderr
    with pytest.raises(ProviderRateLimited):
        with PostgresRequestGate(gate_engine).acquire("test", 300):
            pytest.fail("claim was lost")


def test_missing_gate_table_fails_closed(postgres_database):
    assert postgres_database.upgrade("0005_market_price_not_null").returncode == 0
    engine = create_engine(postgres_database.url)
    try:
        from sqlalchemy.exc import ProgrammingError
        with pytest.raises(ProgrammingError):
            with PostgresRequestGate(engine).acquire("test", 300):
                pytest.fail("request admitted without migration")
    finally:
        engine.dispose()


def test_client_denied_gate_sends_no_feed_request(monkeypatch):
    requests = []
    real_client = httpx.Client

    @contextmanager
    def denied(*args):
        raise ProviderRateLimited("denied")
        yield

    class Gate:
        acquire = staticmethod(denied)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200)), **kwargs
    ))
    client = TravelMidwestClient(gate=Gate())
    client.username = client.password = None
    with pytest.raises(ProviderRateLimited):
        client.fetch_feed("https://example.test/feed")
    assert not requests


def test_client_will_not_lower_provider_minimum():
    with pytest.raises(ValueError, match="300"):
        TravelMidwestClient(min_interval=10)
