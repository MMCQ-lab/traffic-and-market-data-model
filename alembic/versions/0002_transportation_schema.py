"""Add normalized transportation metadata and observations."""

from alembic import op
from src.alt_data.database.base import Base
from src.alt_data.models import all_models  # noqa: F401

revision = "0002_transportation_schema"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=[table for name, table in Base.metadata.tables.items() if name in {
        "transportation_sources", "traffic_cameras", "camera_snapshots", "traffic_sensors", "traffic_observations", "transportation_incidents", "construction_events"
    }])


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), tables=[table for name, table in Base.metadata.tables.items() if name in {
        "construction_events", "transportation_incidents", "traffic_observations", "traffic_sensors", "camera_snapshots", "traffic_cameras", "transportation_sources"
    }])
