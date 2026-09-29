"""Initial ProcessTwin tenant schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-24
"""

import apps.api.processtwin_api.models  # noqa: F401
from alembic import op
from apps.api.processtwin_api.database import Base

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Metadata creates the PostgreSQL-compatible typed UUID/index schema in a single reviewed revision.
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    # Production TimescaleDB deployments may run SELECT create_hypertable('sensor_readings','timestamp', if_not_exists => TRUE)
    # after this revision; SQLite developer mode intentionally remains relational.


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
