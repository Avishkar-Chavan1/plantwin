"""Connector/tag/telemetry tables: guaranteed existence, indexes and tenant row-level security.

Revision ID: 0009_connector_tenant_hardening
Revises: 0008_reconcile_schema
Create Date: 2026-10-08

Why this revision exists
------------------------
* ``connector_tags``, ``connector_tag_mappings`` and ``connector_validation_rules`` (and the
  chunked-import tables) were added after the tenant row-level-security revision (0006), so on
  PostgreSQL they were created *without* an RLS policy. This revision enables RLS with the same
  ``processtwin_tenant_isolation`` policy used by every other tenant table.
* It adds the indexes required by the connector/dashboard read paths.
* Every step is idempotent (checks the live catalog first), so it is safe on a clean database
  (where earlier revisions already built the current schema) and on a database provisioned by an
  older release. It never drops or rewrites data.
"""

from __future__ import annotations

import apps.api.processtwin_api.models  # noqa: F401 - register model metadata
import sqlalchemy as sa
from alembic import op
from apps.api.processtwin_api.database import Base

revision = "0009_connector_tenant_hardening"
down_revision = "0008_reconcile_schema"
branch_labels = None
depends_on = None

_TENANT_TABLES = (
    "connector_tags",
    "connector_tag_mappings",
    "connector_validation_rules",
    "import_jobs",
    "import_chunks",
    "object_storage_artifacts",
)

# (index name, table, columns)
_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_readings_source_timestamp", "sensor_readings", ("data_source_id", "timestamp")),
    ("ix_connector_tags_org_plant", "connector_tags", ("organization_id", "plant_id")),
    ("ix_connector_tag_mappings_sensor", "connector_tag_mappings", ("sensor_id",)),
    (
        "ix_recommendations_org_status_created",
        "recommendations",
        ("organization_id", "status", "created_at"),
    ),
    ("ix_alerts_org_status_occurred", "alerts", ("organization_id", "status", "occurred_at")),
)


def _ensure_tables(bind: sa.engine.Connection) -> None:
    existing = set(sa.inspect(bind).get_table_names())
    for name in _TENANT_TABLES:
        if name not in existing:
            Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def _ensure_indexes(bind: sa.engine.Connection) -> None:
    inspector = sa.inspect(bind)
    for name, table, columns in _INDEXES:
        if table not in inspector.get_table_names():
            continue
        if name in {index["name"] for index in inspector.get_indexes(table)}:
            continue
        op.create_index(name, table, list(columns))


def _ensure_row_level_security(bind: sa.engine.Connection) -> None:
    if bind.dialect.name != "postgresql":
        return
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        policy_exists = bind.execute(
            sa.text(
                "SELECT 1 FROM pg_policies WHERE tablename = :table "
                "AND policyname = 'processtwin_tenant_isolation'"
            ),
            {"table": table},
        ).scalar()
        if not policy_exists:
            op.execute(
                f"CREATE POLICY processtwin_tenant_isolation ON {table} "
                "USING (organization_id = current_setting('app.current_organization_id', true)::uuid) "
                "WITH CHECK (organization_id = current_setting('app.current_organization_id', true)::uuid)"
            )


def upgrade() -> None:
    if op.get_context().as_sql:
        # Offline SQL generation cannot inspect the catalog; statements are state-dependent.
        return
    bind = op.get_bind()
    _ensure_tables(bind)
    _ensure_indexes(bind)
    _ensure_row_level_security(bind)


def downgrade() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS processtwin_tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    inspector = sa.inspect(bind)
    for name, table, _columns in _INDEXES:
        if table in inspector.get_table_names() and name in {
            index["name"] for index in inspector.get_indexes(table)
        }:
            op.drop_index(name, table_name=table)
