"""Measured station weather: extra fields and one row per station observation.

Additive. The deployed weather_observations table has never been populated;
duplicates are removed first anyway so the unique constraint can be added to
any database, keeping the earliest-retrieved copy of each observation.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_weather_station_fields"
down_revision = "0007_camera_created_at"
branch_labels = None
depends_on = None

NEW_COLUMNS = (
    ("station_name", sa.String(length=150)),
    ("dewpoint_c", sa.Numeric(8, 3)),
    ("relative_humidity_pct", sa.Numeric(6, 2)),
    ("wind_speed_kmh", sa.Numeric(8, 3)),
    ("wind_direction_deg", sa.Numeric(6, 2)),
    ("precipitation_last_hour_mm", sa.Numeric(8, 3)),
    ("conditions", sa.String(length=150)),
)


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("weather_observations")}
    for name, column_type in NEW_COLUMNS:
        if name not in existing:
            op.add_column("weather_observations", sa.Column(name, column_type, nullable=True))
    op.execute(sa.text(
        "DELETE FROM weather_observations w USING weather_observations keep "
        "WHERE w.source_id = keep.source_id AND w.station_external_id = keep.station_external_id "
        "AND w.observed_at = keep.observed_at AND (w.retrieved_at, w.id) > (keep.retrieved_at, keep.id)"
    ))
    op.create_unique_constraint(
        "uq_weather_source_station_time", "weather_observations",
        ["source_id", "station_external_id", "observed_at"],
    )
    op.create_index("ix_weather_observed_at", "weather_observations", ["observed_at"])


def downgrade() -> None:
    op.drop_index("ix_weather_observed_at", table_name="weather_observations")
    op.drop_constraint("uq_weather_source_station_time", "weather_observations", type_="unique")
    for name, _ in reversed(NEW_COLUMNS):
        op.drop_column("weather_observations", name)
