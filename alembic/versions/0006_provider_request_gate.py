"""Persist provider request cooldowns independently of ingestion transactions."""
from alembic import op
import sqlalchemy as sa

revision = "0006_provider_request_gate"
down_revision = "0005_market_price_not_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_request_gates",
        sa.Column("provider", sa.String(100), primary_key=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("provider_request_gates")
