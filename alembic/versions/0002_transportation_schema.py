"""Add the immutable Phase 2 transportation schema.

This revision captures the schema introduced before the supplemental camera
fields in 0003. It must not import current ORM metadata.
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_transportation_schema"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transportation_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=False, unique=True),
        sa.Column("agency", sa.String(length=150), nullable=True),
        sa.Column("region", sa.String(length=150), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "traffic_cameras",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("transportation_sources.id"), nullable=False),
        sa.Column("external_camera_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("roadway", sa.String(length=150), nullable=True),
        sa.Column("direction", sa.String(length=20), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("agency", sa.String(length=150), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source_id", "external_camera_id", name="uq_traffic_camera_source_external"),
    )
    op.create_table(
        "camera_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("camera_id", sa.Integer(), sa.ForeignKey("traffic_cameras.id"), nullable=False),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("source_image_url", sa.Text(), nullable=True),
        sa.Column("sha256_hash", sa.String(length=64), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("camera_id", "sha256_hash", name="uq_camera_snapshot_hash"),
    )
    op.create_table(
        "traffic_sensors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("transportation_sources.id"), nullable=False),
        sa.Column("external_sensor_id", sa.String(length=200), nullable=False),
        sa.Column("roadway", sa.String(length=150), nullable=True),
        sa.Column("direction", sa.String(length=20), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("sensor_type", sa.String(length=100), nullable=True),
        sa.Column("agency", sa.String(length=150), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source_id", "external_sensor_id", name="uq_traffic_sensor_source_external"),
    )
    op.create_table(
        "traffic_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sensor_id", sa.Integer(), sa.ForeignKey("traffic_sensors.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("speed_mph", sa.Numeric(8, 3), nullable=True),
        sa.Column("volume", sa.Numeric(12, 3), nullable=True),
        sa.Column("occupancy", sa.Numeric(8, 3), nullable=True),
        sa.Column("travel_time_seconds", sa.Numeric(12, 3), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("sensor_id", "observed_at", name="uq_traffic_observation_sensor_time"),
    )
    op.create_table(
        "transportation_incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("transportation_sources.id"), nullable=False),
        sa.Column("external_incident_id", sa.String(length=200), nullable=False),
        sa.Column("incident_type", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("roadway", sa.String(length=150), nullable=True),
        sa.Column("direction", sa.String(length=20), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source_id", "external_incident_id", name="uq_transport_incident_source_external"),
    )
    op.create_table(
        "construction_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("transportation_sources.id"), nullable=False),
        sa.Column("external_event_id", sa.String(length=200), nullable=False),
        sa.Column("roadway", sa.String(length=150), nullable=True),
        sa.Column("direction", sa.String(length=20), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lanes_closed", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source_id", "external_event_id", name="uq_construction_source_external"),
    )


def downgrade() -> None:
    op.drop_table("construction_events")
    op.drop_table("transportation_incidents")
    op.drop_table("traffic_observations")
    op.drop_table("traffic_sensors")
    op.drop_table("camera_snapshots")
    op.drop_table("traffic_cameras")
    op.drop_table("transportation_sources")
