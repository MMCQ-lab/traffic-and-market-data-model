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
from psycopg import sql
import pytest
from sqlalchemy.engine import make_url


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
        f"prepend_sys_path = {ROOT.as_posix()}\n",
        encoding="utf-8",
    )
    return config_path


def _require_postgres_or_skip(message: str) -> None:
    if os.environ.get("REQUIRE_POSTGRES_TESTS") == "1":
        pytest.fail(message)
    pytest.skip(message)


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
        admin_url = make_url(external_url)
        if admin_url.get_backend_name() != "postgresql" or admin_url.database != "migration_test":
            pytest.fail("TEST_DATABASE_URL must select the dedicated migration_test provisioning database")
        # Never reset the supplied database. Create and drop only a UUID-named
        # database owned by this fixture on the explicitly configured test server.
        name = f"alt_data_test_{uuid.uuid4().hex}"
        with psycopg.connect(admin_url.set(drivername="postgresql").render_as_string(hide_password=False), autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
            try:
                url = admin_url.set(database=name, drivername="postgresql+psycopg").render_as_string(hide_password=False)
                yield PostgresTestDatabase(url, _write_alembic_config(tmp_path, url), tmp_path)
            finally:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        return

    if not _docker_available():
        _require_postgres_or_skip("Docker or TEST_DATABASE_URL is required for PostgreSQL integration tests")

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
        pytest.fail("Could not start disposable PostgreSQL container")

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
