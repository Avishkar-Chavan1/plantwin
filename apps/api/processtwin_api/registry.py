from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from math import isfinite
from typing import Any


def _flatten_numbers(value: Any, prefix: str = "") -> dict[str, float]:
    flattened: dict[str, float] = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            flattened.update(_flatten_numbers(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, (float, int)) and not isinstance(value, bool):
        number = float(value)
        if isfinite(number):
            flattened[prefix[:250]] = number
    return flattened


def log_model_metadata(
    *,
    tracking_uri: str | None,
    organization_id: str,
    model_name: str,
    model_version: str,
    model_id: str,
    dataset_version_id: str | None,
    parameter_set_id: str | None,
    git_sha: str,
    parameters: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> tuple[str | None, str]:
    """Send optional metadata to MLflow; SQL model-version rows remain authoritative."""
    if not tracking_uri:
        return None, "NOT_CONFIGURED"
    try:
        mlflow = import_module("mlflow")
    except ImportError:
        return None, "CLIENT_NOT_INSTALLED"
    try:
        mlflow.set_tracking_uri(tracking_uri)
        experiment_name = f"processtwin/{organization_id}"
        experiment = mlflow.get_experiment_by_name(experiment_name)
        experiment_id = (
            experiment.experiment_id
            if experiment is not None
            else mlflow.create_experiment(experiment_name)
        )
        with mlflow.start_run(
            experiment_id=experiment_id,
            run_name=f"{model_name}-{model_version}",
        ) as run:
            mlflow.set_tags(
                {
                    "processtwin.model_id": model_id,
                    "processtwin.model_name": model_name,
                    "processtwin.model_version": model_version,
                    "processtwin.dataset_version_id": dataset_version_id or "",
                    "processtwin.physics_parameter_set_id": parameter_set_id or "",
                    "git_sha": git_sha,
                }
            )
            numeric_parameters = _flatten_numbers(parameters)
            if numeric_parameters:
                mlflow.log_params(numeric_parameters)
            numeric_metrics = _flatten_numbers(metrics)
            if numeric_metrics:
                mlflow.log_metrics(numeric_metrics)
            return run.info.run_id, "LOGGED"
    except (
        Exception
    ) as exc:  # The relational registry retains the complete record if MLflow is unavailable.
        return None, f"FAILED:{type(exc).__name__}"
