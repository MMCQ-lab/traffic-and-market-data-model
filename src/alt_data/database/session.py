from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from src.alt_data.config.settings import settings

engine = create_engine(settings.resolved_database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Session:
    return SessionLocal()
