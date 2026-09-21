import logging

from src.alt_data.config.settings import settings
from src.alt_data.database.session import get_session
from src.alt_data.ingestion.market import YahooFinanceMarketIngestor

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in settings.market_symbols.split(",") if symbol.strip()))
    if not symbols:
        symbols = [settings.market_symbol.upper()]
    failed = []
    for symbol in symbols:
        try:
            # Independent sessions let later symbols proceed after any failed
            # transaction. A nonzero batch exit still tells systemd about failure.
            with get_session() as session:
                run = YahooFinanceMarketIngestor(
                    session, symbol=symbol, start=settings.market_start_date, end=settings.market_end_date
                ).run()
                logger.info("Finished %s run %s", symbol, run.id)
        except Exception as exc:
            failed.append(symbol)
            logger.error("Market symbol failed: symbol=%s error_type=%s", symbol, type(exc).__name__)
    logger.info("Market batch finished: attempted=%s failed=%s", len(symbols), len(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
