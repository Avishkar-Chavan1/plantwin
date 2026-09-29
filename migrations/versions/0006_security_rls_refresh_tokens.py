"""Add refresh-token rotation state and PostgreSQL tenant row-level-security policies.

Revision ID: 0006_security_rls_refresh_tokens
Revises: 0005_live_readonly_workflow
Create Date: 2026-09-29
"""

import apps.api.processtwin_api.models  # noqa: F401
from alembic import op
from apps.api.processtwin_api.database import Base

revision = "0006_security_rls_refresh_tokens"
down_revision = "0005_live_readonly_workflow"
branch_labels = None
depends_on = None


_TENANT_TABLES = (
    "sites",
    "plants",
    "process_units",
    "equipment",
    "sensors",
    "sensor_readings",
    "data_sources",
    "source_tag_mappings",
    "datasets",
    "dataset_versions",
    "plant_tags",
    "tag_mappings",
    "dataset_observations",
    "physics_parameter_sets",
    "calibration_runs",
    "model_evaluations",
    "model_drift_events",
    "quality_events",
    "twin_states",
    "model_versions",
    "training_runs",
    "simulations",
    "optimization_runs",
    "recommendations",
    "recommendation_reviews",
    "potential_anomalies",
    "alerts",
    "audit_logs",
)


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["refresh_tokens"].create(bind=bind, checkfirst=True)
    if bind.dialect.name != "postgresql":
        return
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY processtwin_tenant_isolation ON {table} "
            "USING (organization_id = current_setting('app.current_organization_id', true)::uuid) "
            "WITH CHECK (organization_id = current_setting('app.current_organization_id', true)::uuid)"
        )
    op.execute("ALTER TABLE organization_memberships ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY processtwin_membership_self ON organization_memberships "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid) "
        "WITH CHECK (user_id = current_setting('app.current_user_id', true)::uuid)"
    )
    op.execute("ALTER TABLE refresh_tokens ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY processtwin_refresh_token_self ON refresh_tokens "
        "USING (user_id = current_setting('app.current_user_id', true)::uuid) "
        "WITH CHECK (user_id = current_setting('app.current_user_id', true)::uuid)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS processtwin_tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        for table, policy in (
            ("organization_memberships", "processtwin_membership_self"),
            ("refresh_tokens", "processtwin_refresh_token_self"),
        ):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    Base.metadata.tables["refresh_tokens"].drop(bind=bind, checkfirst=True)
