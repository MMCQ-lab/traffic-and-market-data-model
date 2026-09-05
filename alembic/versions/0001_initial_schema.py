"""Create Phase 1 ingestion schema."""

from alembic import op
from src.alt_data.database.base import Base
from src.alt_data.models import all_models  # noqa: F401

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    phase1 = {"data_sources", "ingestion_runs", "raw_payloads", "economic_indicators", "camera_locations", "traffic_camera_observations", "roadway_sensor_observations", "weather_observations", "market_prices"}
    Base.metadata.create_all(bind=op.get_bind(), tables=[table for name, table in Base.metadata.tables.items() if name in phase1])


def downgrade() -> None:
    phase1 = {"market_prices", "weather_observations", "roadway_sensor_observations", "traffic_camera_observations", "camera_locations", "economic_indicators", "raw_payloads", "ingestion_runs", "data_sources"}
    Base.metadata.drop_all(bind=op.get_bind(), tables=[table for name, table in Base.metadata.tables.items() if name in phase1])
