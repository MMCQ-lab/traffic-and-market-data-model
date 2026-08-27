"""Create Phase 1 ingestion schema."""

from alembic import op
from src.alt_data.database.base import Base
from src.alt_data.models import all_models  # noqa: F401

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
