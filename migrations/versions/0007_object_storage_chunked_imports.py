"""No-op: import_jobs, import_chunks, object_storage_artifacts created in 0001_initial_schema.

Revision ID: 0007_object_storage_chunked_imports
Revises: 0006_security_rls_refresh_tokens
Create Date: 2026-10-03
"""

from alembic import op

revision = "0007_object_storage_chunked_imports"
down_revision = "0006_security_rls_refresh_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tables already created in 0001_initial_schema via Base.metadata.create_all()
    pass


def downgrade() -> None:
    # No-op - tables managed by initial schema
    pass