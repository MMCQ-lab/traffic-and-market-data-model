from sqlalchemy.engine import make_url
from src.alt_data.config.settings import Settings


def test_connection_components_with_reserved_characters_roundtrip():
    settings = Settings(_env_file=None, database_url=None, postgres_user="a@b",
                        postgres_password="test:@/#%value", postgres_db="test_db")
    url = make_url(settings.resolved_database_url)
    assert url.username == "a@b"
    assert url.password == "test:@/#%value"
