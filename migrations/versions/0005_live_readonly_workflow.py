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


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("recommendations")}
    if "status" in columns:
        op.execute(
            "UPDATE recommendations SET status = 'GENERATED' WHERE status IN ('OPEN', 'CREATED')"
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    for table in ("potential_anomalies", "recommendation_reviews", "source_tag_mappings"):
        if table in tables:
            op.drop_table(table)
