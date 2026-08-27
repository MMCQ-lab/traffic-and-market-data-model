from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "alternative_data"
    postgres_user: str = "alternative_data"
    postgres_password: str = "change_me"
    database_url: str | None = None
    log_level: str = "INFO"
    raw_payload_max_bytes: int = 1_048_576

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"


settings = Settings()
