import logging

from src.alt_data.config.settings import settings
from src.alt_data.database.session import get_session
from src.alt_data.ingestion.market import YahooFinanceMarketIngestor

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
symbols = [symbol.strip().upper() for symbol in settings.market_symbols.split(",") if symbol.strip()]
if not symbols:
    symbols = [settings.market_symbol.upper()]

with get_session() as session:
    for symbol in symbols:
        run = YahooFinanceMarketIngestor(
            session, symbol=symbol, start=settings.market_start_date, end=settings.market_end_date
        ).run()
        logging.getLogger(__name__).info("Finished %s run %s", symbol, run.id)
