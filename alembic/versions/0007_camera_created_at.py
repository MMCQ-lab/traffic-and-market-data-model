"""Reconcile a fresh camera table with the inherited ORM creation timestamp.

Older metadata-created databases already have this column. Preserve their
values. On explicit-DDL installations, first_seen_at is the best existing
record of when a camera entered this system; use it for the backfill.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_camera_created_at"
down_revision = "0006_provider_request_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("traffic_cameras")}
    if "created_at" not in columns:
        op.add_column("traffic_cameras", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
        op.execute(sa.text("UPDATE traffic_cameras SET created_at = first_seen_at"))
        op.alter_column("traffic_cameras", "created_at", nullable=False, server_default=sa.text("now()"))


def downgrade() -> None:
    # The column may predate this revision on deployed databases. Retain it
    # rather than discard original creation timestamps on downgrade.
    pass
