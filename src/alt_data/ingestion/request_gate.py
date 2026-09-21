"""PostgreSQL gate shared by jobs using the same database.

The claim commits before network I/O, so ingestion rollback/process death cannot
erase it. A session advisory lock prevents overlapping requests even when a
request takes longer than the cooldown. Normal completion (including exceptions)
extends the cooldown from completion; a hard kill retains the committed claim.
"""
from contextlib import contextmanager
import hashlib
from math import ceil

from sqlalchemy import text


class ProviderRateLimited(RuntimeError):
    pass


class PostgresRequestGate:
    def __init__(self, engine=None) -> None:
        self.engine = engine

    @contextmanager
    def acquire(self, provider: str, interval_seconds: int):
        if interval_seconds < 300:
            raise ValueError("Travel Midwest request interval must be at least 300 seconds")
        engine = self.engine
        if engine is None:
            from src.alt_data.database.session import engine
        if engine.dialect.name != "postgresql":
            raise RuntimeError("Durable provider gate requires PostgreSQL")
        lock_id = int.from_bytes(hashlib.sha256(provider.encode()).digest()[:8], "big", signed=True)
        with engine.connect() as connection:
            # Session locks outlive commit. Discard the physical connection on exit
            # rather than risking a locked connection being returned to the pool.
            try:
                connection.execute(text("SET statement_timeout = '10s'"))
                locked = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id})
                if not locked:
                    raise ProviderRateLimited("Another provider request is in progress")
                remaining = connection.scalar(text(
                    "SELECT :seconds - EXTRACT(EPOCH FROM (clock_timestamp() - last_attempt_at)) "
                    "FROM provider_request_gates WHERE provider = :provider"
                ), {"seconds": interval_seconds, "provider": provider})
                if remaining is not None and remaining > 0:
                    raise ProviderRateLimited(f"Provider cooldown: retry in {ceil(remaining)}s")
                connection.execute(text(
                    "INSERT INTO provider_request_gates(provider, last_attempt_at) "
                    "VALUES (:provider, clock_timestamp()) "
                    "ON CONFLICT (provider) DO UPDATE SET last_attempt_at = EXCLUDED.last_attempt_at"
                ), {"provider": provider})
                connection.commit()
                try:
                    yield
                finally:
                    connection.execute(text(
                        "UPDATE provider_request_gates SET last_attempt_at = clock_timestamp() "
                        "WHERE provider = :provider"
                    ), {"provider": provider})
                    connection.commit()
            finally:
                # Closing the physical session releases even a lock whose cleanup
                # was interrupted by a database error. Never fail open on DB errors.
                connection.invalidate()
