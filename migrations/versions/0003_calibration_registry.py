"""Add physics calibration, model evaluation and lifecycle provenance.

Revision ID: 0003_calibration_registry
Revises: 0002_historical_datasets
Create Date: 2026-09-29
"""

import apps.api.processtwin_api.models  # noqa: F401
from alembic import op
from apps.api.processtwin_api.database import Base
from sqlalchemy import JSON, Column, ForeignKey, String, Uuid, inspect

revision = "0003_calibration_registry"
down_revision = "0002_historical_datasets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("model_versions")}
    additions = {
        "training_period": JSON(),
        "validation_period": JSON(),
        "test_period": JSON(),
        "hyperparameters": JSON(),
        "operating_envelope": JSON(),
        "git_sha": String(80),
        "mlflow_run_id": String(64),
        "created_by": Uuid(),
        "dataset_version_id": Uuid(),
        "physics_parameter_set_id": Uuid(),
    }
    for name, column_type in additions.items():
        if name not in columns:
            if name in {"training_period", "validation_period", "test_period", "hyperparameters", "operating_envelope"}:
                op.add_column(
                    "model_versions",
                    Column(name, column_type, nullable=False, server_default="{}"),
                )
            else:
                references = {
                    "created_by": "users.id",
                    "dataset_version_id": "dataset_versions.id",
                    "physics_parameter_set_id": "physics_parameter_sets.id",
                }
                foreign_keys = (
                    [ForeignKey(references[name], ondelete="SET NULL")]
                    if name in references
                    else []
                )
                op.add_column(
                    "model_versions", Column(name, column_type, *foreign_keys, nullable=True)
                )
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    for table in ("model_drift_events", "model_evaluations", "calibration_runs", "physics_parameter_sets"):
        if table in tables:
            op.drop_table(table)
    columns = {column["name"] for column in inspect(bind).get_columns("model_versions")}
    for name in (
        "physics_parameter_set_id",
        "dataset_version_id",
        "created_by",
        "git_sha",
        "mlflow_run_id",
        "operating_envelope",
        "hyperparameters",
        "test_period",
        "validation_period",
        "training_period",
    ):
        if name in columns:
            op.drop_column("model_versions", name)