"""Add complete OHLCV fields and market-price idempotency."""

from alembic import op
import sqlalchemy as sa

revision = "0004_market_price_fields"
down_revision = "0003_camera_feed_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {column["name"] for column in inspector.get_columns("market_prices")}
    columns = {
        "open": sa.Numeric(24, 6),
        "high": sa.Numeric(24, 6),
        "low": sa.Numeric(24, 6),
        "adjusted_close": sa.Numeric(24, 6),
        "volume": sa.BigInteger(),
    }
    for name, column_type in columns.items():
        if name not in existing:
            op.add_column("market_prices", sa.Column(name, column_type, nullable=True))
    constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("market_prices")}
    if "uq_market_price_source_symbol_time" not in constraints:
        op.create_unique_constraint(
            "uq_market_price_source_symbol_time", "market_prices", ["source_id", "symbol", "observed_at"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("market_prices")}
    if "uq_market_price_source_symbol_time" in constraints:
        op.drop_constraint("uq_market_price_source_symbol_time", "market_prices", type_="unique")
    existing = {column["name"] for column in inspector.get_columns("market_prices")}
    for name in ("volume", "adjusted_close", "low", "high", "open"):
        if name in existing:
            op.drop_column("market_prices", name)
