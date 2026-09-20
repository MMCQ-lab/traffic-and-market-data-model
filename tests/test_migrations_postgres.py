"""PostgreSQL-only migration integration tests using a disposable Docker DB."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

import psycopg
import pytest


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_OHLCV = ("open", "high", "low", "adjusted_close", "volume")


class PostgresTestDatabase:
    def __init__(self, url: str, config_path: Path, workdir: Path) -> None:
        self.url = url
        self.config_path = config_path
        self.workdir = workdir

    @property
    def psycopg_url(self) -> str:
        return self.url.replace("postgresql+psycopg://", "postgresql://", 1)

    def connect(self):
        return psycopg.connect(self.psycopg_url)

    def upgrade(self, target: str = "head", future_metadata_probe: bool = False) -> subprocess.CompletedProcess[str]:
        code = (
            """
import sys
from alembic import command
from alembic.config import Config
from sqlalchemy import Column, Integer, Table
from src.alt_data.database.base import Base

Table('future_metadata_probe', Base.metadata, Column('id', Integer, primary_key=True))
command.upgrade(Config(sys.argv[1]), sys.argv[2])
"""
            if future_metadata_probe
            else """
import sys
from alembic import command
from alembic.config import Config

command.upgrade(Config(sys.argv[1]), sys.argv[2])
"""
        )
        environment = os.environ.copy()
        environment["DATABASE_URL"] = self.url
        environment["PYTHONPATH"] = str(ROOT)
        return subprocess.run(
            [sys.executable, "-c", code, str(self.config_path), target],
            cwd=self.workdir,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )


def _write_alembic_config(tmp_path: Path, url: str) -> Path:
    config_path = tmp_path / "alembic.ini"
    config_path.write_text(
        "[alembic]\n"
        f"script_location = {(ROOT / 'alembic').as_posix()}\n"
        f"prepend_sys_path = {ROOT.as_posix()}\n"
        f"sqlalchemy.url = {url}\n",
        encoding="utf-8",
    )
    return config_path


def _reset_test_database(database: PostgresTestDatabase) -> None:
    """Reset only the explicitly supplied disposable test database."""
    with database.connect() as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cursor.execute("CREATE SCHEMA public")


def _unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _docker_available() -> bool:
    return shutil.which("docker") is not None and subprocess.run(
        ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    ).returncode == 0


@pytest.fixture()
def postgres_database(tmp_path: Path) -> PostgresTestDatabase:
    external_url = os.environ.get("TEST_DATABASE_URL")
    if external_url:
        database = PostgresTestDatabase(external_url, _write_alembic_config(tmp_path, external_url), tmp_path)
        _reset_test_database(database)
        try:
            yield database
        finally:
            _reset_test_database(database)
        return

    if not _docker_available():
        pytest.skip("Docker is required for PostgreSQL migration integration tests")

    port = _unused_local_port()
    container_name = f"alt-data-migration-test-{uuid.uuid4().hex}"
    started = subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", container_name,
            "--env", "POSTGRES_DB=migration_test",
            "--env", "POSTGRES_USER=migration_test",
            "--env", "POSTGRES_PASSWORD=migration_test",
            "--publish", f"127.0.0.1:{port}:5432",
            "postgres:16",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.skip(f"Could not start disposable PostgreSQL container: {started.stderr.strip()}")

    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            ready = subprocess.run(
                ["docker", "exec", container_name, "pg_isready", "-U", "migration_test", "-d", "migration_test"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            pytest.fail("Disposable PostgreSQL container did not become ready")

        url = f"postgresql+psycopg://migration_test:migration_test@127.0.0.1:{port}/migration_test"
        yield PostgresTestDatabase(url, _write_alembic_config(tmp_path, url), tmp_path)
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def _current_revision(database: PostgresTestDatabase) -> str:
    with database.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM alembic_version")
        return cursor.fetchone()[0]


def _required_nullability(database: PostgresTestDatabase) -> dict[str, str]:
    with database.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'market_prices' "
            "AND column_name = ANY(%s)",
            (list(REQUIRED_OHLCV),),
        )
        return dict(cursor.fetchall())


def _insert_market_price(database: PostgresTestDatabase, *, open_value: str | None) -> None:
    with database.connect() as connection, connection.cursor() as cursor:
        cursor.execute("INSERT INTO data_sources (name, base_url) VALUES ('migration_test', 'https://example.test') RETURNING id")
        source_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO market_prices "
            "(source_id, symbol, observed_at, retrieved_at, open, high, low, close, adjusted_close, volume) "
            "VALUES (%s, 'SPY', '2025-01-02T00:00:00+00:00', '2025-01-03T00:00:00+00:00', "
            "%s, 101.0, 99.0, 100.0, 100.0, 1000)",
            (source_id, open_value),
        )
        connection.commit()


def test_empty_postgres_upgrades_from_zero_to_head_with_final_contract(postgres_database: PostgresTestDatabase) -> None:
    result = postgres_database.upgrade()
    assert result.returncode == 0, result.stderr
    assert _current_revision(postgres_database) == "0005_market_price_not_null"
    assert _required_nullability(postgres_database) == {column: "NO" for column in REQUIRED_OHLCV}
    with postgres_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid = 'market_prices'::regclass AND contype = 'u'")
        assert "uq_market_price_source_symbol_time" in {row[0] for row in cursor.fetchall()}


def test_current_0004_database_upgrades_in_place_and_preserves_rows(postgres_database: PostgresTestDatabase) -> None:
    assert postgres_database.upgrade("0004_market_price_fields").returncode == 0
    _insert_market_price(postgres_database, open_value="100.0")
    assert _required_nullability(postgres_database)["open"] == "YES"

    result = postgres_database.upgrade()
    assert result.returncode == 0, result.stderr
    assert _current_revision(postgres_database) == "0005_market_price_not_null"
    assert _required_nullability(postgres_database) == {column: "NO" for column in REQUIRED_OHLCV}
    with postgres_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT symbol, open, high, low, close, adjusted_close, volume FROM market_prices")
        assert cursor.fetchall() == [("SPY", 100.0, 101.0, 99.0, 100.0, 100.0, 1000)]


def test_current_0004_database_with_null_ohlcv_fails_before_schema_change(postgres_database: PostgresTestDatabase) -> None:
    assert postgres_database.upgrade("0004_market_price_fields").returncode == 0
    _insert_market_price(postgres_database, open_value=None)

    result = postgres_database.upgrade()
    assert result.returncode != 0
    assert "have NULL required OHLCV values" in (result.stdout + result.stderr)
    assert _current_revision(postgres_database) == "0004_market_price_fields"
    assert _required_nullability(postgres_database)["open"] == "YES"
    with postgres_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM market_prices WHERE open IS NULL")
        assert cursor.fetchone()[0] == 1


def test_future_base_metadata_cannot_create_historical_tables(postgres_database: PostgresTestDatabase) -> None:
    result = postgres_database.upgrade(future_metadata_probe=True)
    assert result.returncode == 0, result.stderr
    with postgres_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.future_metadata_probe')")
        assert cursor.fetchone()[0] is None


@pytest.mark.parametrize("revision", ["0001_initial_schema.py", "0002_transportation_schema.py"])
def test_historical_migrations_do_not_import_application_orm_metadata(revision: str) -> None:
    source = (ROOT / "alembic" / "versions" / revision).read_text(encoding="utf-8")
    assert "src.alt_data" not in source
    assert "Base.metadata" not in source
    assert "create_all" not in source
