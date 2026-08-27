import logging

from sqlalchemy import func, select

from src.alt_data.config.settings import settings
from src.alt_data.database.session import get_session
from src.alt_data.models import EconomicIndicator, IngestionRun

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

with get_session() as session:
    count = session.scalar(select(func.count()).select_from(EconomicIndicator))
    successful_runs = session.scalar(select(func.count()).select_from(IngestionRun).where(IngestionRun.status == "success"))
    logger.info("economic_indicators: %s", count)
    logger.info("successful ingestion_runs: %s", successful_runs)
