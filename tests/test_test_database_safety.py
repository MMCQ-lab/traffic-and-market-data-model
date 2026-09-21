import pytest

from tests import postgres_support


def test_test_fixture_rejects_application_database_before_connecting(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://fixture:fixture@localhost/alternative_data")

    def forbidden_connection(*args, **kwargs):
        pytest.fail("Must reject unsafe test configuration before connecting")

    monkeypatch.setattr(postgres_support.psycopg, "connect", forbidden_connection)
    fixture = postgres_support.postgres_database.__wrapped__(tmp_path)
    with pytest.raises(pytest.fail.Exception, match="dedicated migration_test"):
        next(fixture)


def test_ci_fails_instead_of_skipping_missing_postgres(monkeypatch, tmp_path):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("REQUIRE_POSTGRES_TESTS", "1")
    monkeypatch.setattr(postgres_support, "_docker_available", lambda: False)
    fixture = postgres_support.postgres_database.__wrapped__(tmp_path)
    with pytest.raises(pytest.fail.Exception, match="Docker or TEST_DATABASE_URL"):
        next(fixture)
