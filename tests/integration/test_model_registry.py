from __future__ import annotations

from apps.api.processtwin_api.registry import log_model_metadata


def test_sql_registry_metadata_can_be_recorded_without_optional_mlflow() -> None:
    run_id, status = log_model_metadata(
        tracking_uri=None,
        organization_id="tenant-a",
        model_name="CSTR physics",
        model_version="1",
        model_id="model-1",
        dataset_version_id="dataset-1",
        parameter_set_id="parameters-1",
        git_sha="deadbeef",
        parameters={"temperature": {"value": 453.15, "unit": "K"}},
        metrics={"test": {"mae": 1.25, "r2": 0.95}},
    )
    assert run_id is None
    assert status == "NOT_CONFIGURED"
