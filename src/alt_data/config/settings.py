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
    travel_midwest_username: str | None = None
    travel_midwest_password: str | None = None
    travel_midwest_traffic_feed_url: str | None = None
    travel_midwest_camera_feed_url: str | None = None
    travel_midwest_incident_feed_url: str | None = None
    travel_midwest_construction_feed_url: str | None = None
    travel_midwest_sensor_feed_url: str | None = None
    camera_storage_path: str = "data/cameras"
    travel_midwest_min_interval_seconds: int = 300
    market_symbols: str = "SPY,QQQ,DIA,IWM,IYT,XLI,AMZN,UPS,FDX,WMT,TGT,COST,XPO,CHRW,DAL,UAL,LUV,AAL,UNP,CSX,JBHT"
    # Kept for backwards-compatible single-symbol runs.
    market_symbol: str = "SPY"
    market_start_date: str = "2010-01-01"
    market_end_date: str | None = None

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"


settings = Settings()
