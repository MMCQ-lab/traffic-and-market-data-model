"""Real PostgreSQL migration contracts; fixture is shared with runtime tests."""
import pytest

from tests.postgres_support import PostgresTestDatabase, ROOT, REQUIRED_OHLCV


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
    assert _current_revision(postgres_database) == "0007_camera_created_at"
    assert _required_nullability(postgres_database) == {column: "NO" for column in REQUIRED_OHLCV}
    with postgres_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid = 'market_prices'::regclass AND contype = 'u'")
        assert "uq_market_price_source_symbol_time" in {row[0] for row in cursor.fetchall()}


def test_current_0004_database_upgrades_in_place_and_preserves_rows(postgres_database: PostgresTestDatabase) -> None:
    assert postgres_database.upgrade("0004_market_price_fields").returncode == 0
    _insert_market_price(postgres_database, open_value="100.0")
    assert _required_nullability(postgres_database)["open"] == "YES"

    result = postgres_database.upgrade("0005_market_price_not_null")
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


def test_migrated_tables_match_model_columns_and_nullability(postgres_database):
    from sqlalchemy import create_engine, inspect
    from src.alt_data.database.base import Base
    from src.alt_data.models import all_models  # noqa: F401
    assert postgres_database.upgrade().returncode == 0
    engine = create_engine(postgres_database.url)
    try:
        inspector = inspect(engine)
        for table in Base.metadata.sorted_tables:
            actual = {column["name"]: column["nullable"] for column in inspector.get_columns(table.name)}
            expected = {column.name: column.nullable for column in table.columns}
            assert actual == expected, table.name
    finally:
        engine.dispose()


@pytest.mark.parametrize("legacy_column", [False, True])
def test_camera_timestamp_reconciliation_preserves_metadata(postgres_database, legacy_column):
    assert postgres_database.upgrade("0006_provider_request_gate").returncode == 0
    with postgres_database.connect() as connection:
        connection.execute("INSERT INTO transportation_sources (name, source_type, source_url) VALUES ('fixture', 'camera', 'https://example.test')")
        connection.execute("INSERT INTO traffic_cameras (source_id, external_camera_id, active, first_seen_at, last_seen_at) VALUES (1, 'cam', true, '2025-01-01T00:00:00+00:00', '2025-01-02T00:00:00+00:00')")
        if legacy_column:
            connection.execute("ALTER TABLE traffic_cameras ADD COLUMN created_at timestamptz NOT NULL DEFAULT '2024-12-31T00:00:00+00:00'")
    result = postgres_database.upgrade()
    assert result.returncode == 0, result.stderr
    with postgres_database.connect() as connection:
        rows = connection.execute("SELECT external_camera_id, created_at::date::text FROM traffic_cameras").fetchall()
        assert rows == [("cam", "2024-12-31" if legacy_column else "2025-01-01")]


def test_market_unique_constraint_actually_rejects_duplicate(postgres_database):
    import psycopg
    assert postgres_database.upgrade().returncode == 0
    _insert_market_price(postgres_database, open_value="100.0")
    with postgres_database.connect() as connection:
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute("INSERT INTO market_prices (source_id, symbol, observed_at, retrieved_at, open, high, low, close, adjusted_close, volume) SELECT source_id, symbol, observed_at, retrieved_at, open, high, low, close, adjusted_close, volume FROM market_prices")


@pytest.mark.parametrize("revision", ["0001_initial_schema.py", "0002_transportation_schema.py"])
def test_historical_migrations_do_not_import_application_orm_metadata(revision: str) -> None:
    source = (ROOT / "alembic" / "versions" / revision).read_text(encoding="utf-8")
    assert "src.alt_data" not in source
    assert "Base.metadata" not in source
    assert "create_all" not in source
