import logging

from src.alt_data.config.settings import settings
from src.alt_data.database.session import get_session
from src.alt_data.ingestion.traffic import TravelMidwestLinkTrafficIngestor


def main() -> int:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    with get_session() as session:
        TravelMidwestLinkTrafficIngestor(session).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
