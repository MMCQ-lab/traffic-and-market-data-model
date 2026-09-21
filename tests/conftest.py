"""Never load developer dotenv files while collecting or running tests."""
import os

os.environ["ALT_DATA_ENV_FILE"] = ""
# Integration tests use TEST_DATABASE_URL explicitly, never the application URL.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from tests.postgres_support import postgres_database  # noqa: E402,F401


def pytest_sessionfinish(session, exitstatus):
    # A green CI job must include PostgreSQL validation, not just unit tests.
    if os.environ.get("REQUIRE_POSTGRES_TESTS") == "1":
        reporter = session.config.pluginmanager.getplugin("terminalreporter")
        if reporter is not None and reporter.stats.get("skipped"):
            session.exitstatus = 1
