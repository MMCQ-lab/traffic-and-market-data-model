"""Reconcile market-price nullability after metadata-driven history.

Production databases already at 0004 are upgraded in place. This revision
never recreates ``market_prices`` and stops before DDL if existing data cannot
satisfy the required OHLCV contract.
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_market_price_not_null"
down_revision = "0004_market_price_fields"
branch_labels = None
depends_on = None


REQUIRED_COLUMNS = {
    "open": sa.Numeric(24, 6),
    "high": sa.Numeric(24, 6),
    "low": sa.Numeric(24, 6),
    "adjusted_close": sa.Numeric(24, 6),
    "volume": sa.BigInteger(),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "market_prices" not in inspector.get_table_names():
        raise RuntimeError("Cannot reconcile market_prices: table is missing")

    existing_columns = {column["name"] for column in inspector.get_columns("market_prices")}
    missing_columns = sorted(set(REQUIRED_COLUMNS) - existing_columns)
    if missing_columns:
        raise RuntimeError(
            "Cannot reconcile market_prices: missing required columns " + ", ".join(missing_columns)
        )

    null_count = bind.execute(
        sa.text(
            "SELECT count(*) FROM market_prices "
            "WHERE open IS NULL OR high IS NULL OR low IS NULL "
            "OR adjusted_close IS NULL OR volume IS NULL"
        )
    ).scalar_one()
    if null_count:
        raise RuntimeError(
            f"Cannot reconcile market_prices: {null_count} row(s) have NULL required OHLCV values"
        )

    for column_name, column_type in REQUIRED_COLUMNS.items():
        op.alter_column("market_prices", column_name, existing_type=column_type, nullable=False)


def downgrade() -> None:
    for column_name, column_type in REQUIRED_COLUMNS.items():
        op.alter_column("market_prices", column_name, existing_type=column_type, nullable=True)
