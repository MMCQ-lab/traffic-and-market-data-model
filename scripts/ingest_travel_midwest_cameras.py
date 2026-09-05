import logging

from src.alt_data.config.settings import settings
from src.alt_data.database.session import get_session
from src.alt_data.ingestion.traffic import TravelMidwestCameraIngestor

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

with get_session() as session:
    TravelMidwestCameraIngestor(session).run()
