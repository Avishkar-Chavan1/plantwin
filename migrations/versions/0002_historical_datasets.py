"""Add versioned historical datasets and measurement unit provenance.

Revision ID: 0002_historical_datasets
Revises: 0001_initial_schema
Create Date: 2026-09-29
"""

import apps.api.processtwin_api.models  # noqa: F401
from alembic import op
from apps.api.processtwin_api.database import Base
from sqlalchemy import JSON, Column, Float, String, inspect

revision = "0002_historical_datasets"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("sensor_readings")}
    additions = {
        "original_value": Float(),
        "original_unit": String(32),
        "normalized_value": Float(),
        "normalized_unit": String(32),
        "quality_reasons": JSON(),
    }
    for name, column_type in additions.items():
        if name not in columns:
            if name == "quality_reasons":
                op.add_column(
                    "sensor_readings",
                    Column(name, column_type, nullable=False, server_default="[]"),
                )
            else:
                op.add_column("sensor_readings", Column(name, column_type, nullable=True))
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    for table in (
        "dataset_observations",
        "tag_mappings",
        "plant_tags",
        "dataset_versions",
        "datasets",
        "data_sources",
    ):
        if table in tables:
            op.drop_table(table)
    columns = {column["name"] for column in inspect(bind).get_columns("sensor_readings")}
    for name in (
        "quality_reasons",
        "normalized_unit",
        "normalized_value",
        "original_unit",
        "original_value",
    ):
        if name in columns:
            op.drop_column("sensor_readings", name)
