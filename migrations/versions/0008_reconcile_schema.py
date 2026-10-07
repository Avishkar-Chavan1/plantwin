"""Reconcile the database schema with the current ORM metadata.

Revision ID: 0008_reconcile_schema
Revises: 660c4eb0949d
Create Date: 2026-10-06

Databases provisioned before a model change can drift: ``Base.metadata.create_all``
only creates missing tables and never alters existing ones, so columns added to the
ORM (for example ``recommendations.baseline``) can be absent while the alembic
version already points at head. Every API query touching the drifted table then
fails with "no such column". This revision inspects the live schema and adds any
table or column declared by the ORM that the database is missing, so existing
deployments converge to the models without touching data.
"""

import apps.api.processtwin_api.models  # noqa: F401 - register model metadata
from alembic import op
from apps.api.processtwin_api.database import Base
from sqlalchemy import JSON, Boolean, Column, Numeric, inspect, text

revision = "0008_reconcile_schema"
down_revision = "660c4eb0949d"
branch_labels = None
depends_on = None

_JSON_TYPES = (JSON,)
_STRING_DEFAULT_FALLBACK = ""


def _server_default_for(column: Column) -> str | None:
    """Derive a safe server default so NOT NULL columns can be added to populated tables."""
    if column.nullable:
        return None
    default = column.default
    if default is not None and getattr(default, "is_scalar", False) and default.arg is not None:
        return repr(default.arg) if isinstance(default.arg, str) else str(default.arg)
    python_type = column.type.python_type if column.type is not None else None
    if isinstance(column.type, (*_JSON_TYPES,)):
        return "'{}'"
    if isinstance(column.type, Boolean):
        return "'0'"
    if python_type is None:
        return None
    if python_type is str:
        return repr(_STRING_DEFAULT_FALLBACK)
    if python_type in (int, float) or isinstance(column.type, Numeric):
        return "'0'"
    return None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            table.create(bind)
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            server_default = _server_default_for(column)
            op.add_column(
                table.name,
                Column(
                    column.name,
                    column.type,
                    nullable=column.nullable,
                    server_default=server_default,
                ),
            )

    # Show that the revision ran even when the schema was already current.
    op.execute(text("SELECT 1"))


def downgrade() -> None:
    # Reconciliation only converges a database toward the ORM; the inverse state is
    # the pre-migration drift, which cannot be reconstructed reliably. Intentionally a no-op.
    pass
