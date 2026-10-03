"""Add live read-only source health and synchronized twin workflow.

Revision ID: 0005_live_readonly_workflow
Revises: 0004_readonly_twin_workflow
Create Date: 2026-09-29
"""

import apps.api.processtwin_api.models  # noqa: F401
from alembic import op
from apps.api.processtwin_api.database import Base
from sqlalchemy import inspect


revision = "0005_live_readonly_workflow"
down_revision = "0004_readonly_twin_workflow"
branch_labels = None
depends_on = None


def _get_columns(table_name: str) -> set[str]:
    """Get column names, compatible with offline mode."""
    context = op.get_context()
    if context.as_sql:
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
    bind = op.get_bind()
    if not op.get_context().as_sql:
        Base.metadata.create_all(bind=bind)
    columns = _get_columns("recommendations")
    if "status" in columns:
        op.execute(
            "UPDATE recommendations SET status = 'GENERATED' WHERE status IN ('OPEN', 'CREATED')"
        )


def downgrade() -> None:
    for table in ("potential_anomalies", "recommendation_reviews", "source_tag_mappings"):
        if _table_exists(table):
            op.drop_table(table)