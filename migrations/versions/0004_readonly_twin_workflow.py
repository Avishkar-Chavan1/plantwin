"""Add read-only source health and end-to-end digital-twin provenance.

Revision ID: 0004_readonly_twin_workflow
Revises: 0003_calibration_registry
Create Date: 2026-09-29
"""

from alembic import op
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, Uuid, inspect

from apps.api.processtwin_api.database import Base
import apps.api.processtwin_api.models  # noqa: F401

revision = "0004_readonly_twin_workflow"
down_revision = "0003_calibration_registry"
branch_labels = None
depends_on = None


def _add_columns(table: str, additions: dict[str, Column]) -> None:
    bind = op.get_bind()
    present = {column["name"] for column in inspect(bind).get_columns(table)}
    for name, column in additions.items():
        if name not in present:
            op.add_column(table, column)


def upgrade() -> None:
    _add_columns(
        "data_sources",
        {
            "status": Column("status", String(20), nullable=False, server_default="DISCONNECTED"),
            "last_success_at": Column("last_success_at", DateTime(timezone=True)),
            "last_message_at": Column("last_message_at", DateTime(timezone=True)),
            "message_rate_per_minute": Column("message_rate_per_minute", Float(), nullable=False, server_default="0"),
            "latency_ms": Column("latency_ms", Float()),
            "error_count": Column("error_count", Integer(), nullable=False, server_default="0"),
            "freshness_s": Column("freshness_s", Float()),
            "last_error": Column("last_error", Text()),
        },
    )
    _add_columns(
        "sensor_readings",
        {"data_source_id": Column("data_source_id", Uuid(), ForeignKey("data_sources.id", ondelete="SET NULL"))},
    )
    _add_columns(
        "twin_states",
        {
            "source_mode": Column("source_mode", String(24), nullable=False, server_default="SIMULATION"),
            "source_ids": Column("source_ids", JSON(), nullable=False, server_default="[]"),
            "model_version_id": Column("model_version_id", Uuid(), ForeignKey("model_versions.id", ondelete="SET NULL")),
            "physics_parameter_set_id": Column("physics_parameter_set_id", Uuid(), ForeignKey("physics_parameter_sets.id", ondelete="SET NULL")),
            "data_quality": Column("data_quality", JSON(), nullable=False, server_default="{}"),
            "prediction_status": Column("prediction_status", String(40), nullable=False, server_default="PREDICTED"),
            "uncertainty": Column("uncertainty", JSON(), nullable=False, server_default="{}"),
            "health_factors": Column("health_factors", JSON(), nullable=False, server_default="{}"),
        },
    )
    _add_columns(
        "simulations",
        {
            "user_id": Column("user_id", Uuid(), ForeignKey("users.id", ondelete="SET NULL")),
            "model_version_id": Column("model_version_id", Uuid(), ForeignKey("model_versions.id", ondelete="SET NULL")),
            "physics_parameter_set_id": Column("physics_parameter_set_id", Uuid(), ForeignKey("physics_parameter_sets.id", ondelete="SET NULL")),
            "operating_envelope": Column("operating_envelope", JSON(), nullable=False, server_default="{}"),
            "warnings": Column("warnings", JSON(), nullable=False, server_default="[]"),
        },
    )
    _add_columns(
        "optimization_runs",
        {
            "user_id": Column("user_id", Uuid(), ForeignKey("users.id", ondelete="SET NULL")),
            "model_version_id": Column("model_version_id", Uuid(), ForeignKey("model_versions.id", ondelete="SET NULL")),
            "physics_parameter_set_id": Column("physics_parameter_set_id", Uuid(), ForeignKey("physics_parameter_sets.id", ondelete="SET NULL")),
            "algorithm": Column("algorithm", String(80), nullable=False, server_default="differential_evolution"),
            "bounds": Column("bounds", JSON(), nullable=False, server_default="{}"),
            "constraints": Column("constraints", JSON(), nullable=False, server_default="{}"),
            "uncertainty": Column("uncertainty", JSON(), nullable=False, server_default="{}"),
        },
    )
    _add_columns(
        "recommendations",
        {
            "status": Column("status", String(32), nullable=False, server_default="GENERATED"),
            "simulation_id": Column("simulation_id", Uuid(), ForeignKey("simulations.id", ondelete="SET NULL")),
            "optimization_run_id": Column("optimization_run_id", Uuid(), ForeignKey("optimization_runs.id", ondelete="SET NULL")),
            "model_version_id": Column("model_version_id", Uuid(), ForeignKey("model_versions.id", ondelete="SET NULL")),
            "baseline": Column("baseline", JSON(), nullable=False, server_default="{}"),
            "proposed_change": Column("proposed_change", JSON(), nullable=False, server_default="{}"),
            "energy_impact": Column("energy_impact", JSON(), nullable=False, server_default="{}"),
            "uncertainty": Column("uncertainty", JSON(), nullable=False, server_default="{}"),
            "constraint_status": Column("constraint_status", String(32), nullable=False, server_default="UNKNOWN"),
            "expires_at": Column("expires_at", DateTime(timezone=True)),
        },
    )
    Base.metadata.create_all(bind=op.get_bind())
    # Existing open demo recommendations are retained as generated, advisory records.
    op.execute("UPDATE recommendations SET status = 'GENERATED' WHERE status = 'OPEN'")


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    for table in ("potential_anomalies", "recommendation_reviews", "source_tag_mappings"):
        if table in tables:
            op.drop_table(table)
    columns_to_drop = {
        "recommendations": ("expires_at", "constraint_status", "uncertainty", "energy_impact", "proposed_change", "baseline", "model_version_id", "optimization_run_id", "simulation_id"),
        "optimization_runs": ("uncertainty", "constraints", "bounds", "algorithm", "physics_parameter_set_id", "model_version_id", "user_id"),
        "simulations": ("warnings", "operating_envelope", "physics_parameter_set_id", "model_version_id", "user_id"),
        "twin_states": ("health_factors", "uncertainty", "prediction_status", "data_quality", "physics_parameter_set_id", "model_version_id", "source_ids", "source_mode"),
        "sensor_readings": ("data_source_id",),
        "data_sources": ("last_error", "freshness_s", "error_count", "latency_ms", "message_rate_per_minute", "last_message_at", "last_success_at", "status"),
    }
    for table, names in columns_to_drop.items():
        columns = {column["name"] for column in inspect(bind).get_columns(table)}
        for name in names:
            if name in columns:
                op.drop_column(table, name)