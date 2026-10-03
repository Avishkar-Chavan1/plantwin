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


def _get_columns(table_name: str) -> set[str]:
    """Get column names, compatible with offline mode."""
    context = op.get_context()
    if context.as_sql:
        # In offline mode, assume columns don't exist (will be added)
        return set()
    bind = op.get_bind()
    return {column["name"] for column in inspect(bind).get_columns(table_name)}


def _table_exists(table_name: str) -> bool:
    """Check if table exists, compatible with offline mode."""
    context = op.get_context()
    if context.as_sql:
        return True
    bind = op.get_bind()
    return table_name in inspect(bind).get_table_names()


def upgrade() -> None:
    columns = _get_columns("sensor_readings")
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
    bind = op.get_bind()
    if not op.get_context().as_sql:
        Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    for table in (
        "dataset_observations",
        "tag_mappings",
        "plant_tags",
        "dataset_versions",
        "datasets",
        "data_sources",
    ):
        if _table_exists(table):
            op.drop_table(table)
    columns = _get_columns("sensor_readings")
    for name in (
        "quality_reasons",
        "normalized_unit",
        "normalized_value",
        "original_unit",
        "original_value",
    ):
        if name in columns:
            op.drop_column("sensor_readings", name)