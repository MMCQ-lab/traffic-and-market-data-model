"""Store additional documented camera-report fields."""

from alembic import op
import sqlalchemy as sa

revision = "0003_camera_feed_fields"
down_revision = "0002_transportation_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0002 uses SQLAlchemy metadata to create the table, so on a fresh
    # database these columns may already be present. Keep this migration
    # safe for both fresh and previously-created Phase 2 databases.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {column["name"] for column in inspector.get_columns("traffic_cameras")}
    columns = {
        "video_url": sa.Text(),
        "warning_age": sa.Boolean(),
        "too_old": sa.Boolean(),
        "age_minutes": sa.Integer(),
    }
    for name, column_type in columns.items():
        if name not in existing:
            op.add_column("traffic_cameras", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {column["name"] for column in inspector.get_columns("traffic_cameras")}
    for name in ("age_minutes", "too_old", "warning_age", "video_url"):
        if name in existing:
            op.drop_column("traffic_cameras", name)
