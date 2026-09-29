from __future__ import annotations

from apps.api.processtwin_api.database import SessionLocal
from apps.api.processtwin_api.models import (
    Dataset,
    DatasetVersion,
    ModelEvaluation,
    ModelVersion,
    OrganizationMembership,
    RoleName,
)
from packages.calibration import parameter_catalog
from tests.integration.test_historical_datasets import tenant_client


def test_validation_requires_actual_evaluation_and_governed_stage() -> None:
    client, headers, plant, _ = tenant_client()
    with SessionLocal() as session:
        membership = session.query(OrganizationMembership).one()
        membership.role = RoleName.ADMIN
        dataset = Dataset(
            organization_id=membership.organization_id,
            plant_id=plant.id,
            name="Independent evaluation fixture",
        )
        session.add(dataset)
        session.flush()
        version = DatasetVersion(
            organization_id=membership.organization_id,
            dataset_id=dataset.id,
            version=1,
            status="IMPORTED",
            source_filename="validation-fixture.csv",
            checksum_sha256="0" * 64,
            row_count=12,
            measurement_count=12,
            quality_summary={},
        )
        session.add(version)
        session.flush()
        model = ModelVersion(
            organization_id=membership.organization_id,
            name="Reviewed physics model",
            version="1",
            model_type="physics_cstr",
            status="EVALUATED",
            feature_schema={"units": "SI"},
            target_schema={"outputs": ["temperature_k"]},
            metrics={},
            dataset_version_id=version.id,
            operating_envelope={"temperature_k": {"minimum": 443.15, "maximum": 463.15}},
        )
        session.add_all([version, model])
        session.flush()
        evaluation = ModelEvaluation(
            organization_id=membership.organization_id,
            model_version_id=model.id,
            dataset_version_id=version.id,
            evaluation_type="PHYSICS_ONLY",
            metrics={"temperature_k": {"mae": 0.3, "rmse": 0.4, "r2": 0.98, "bias": 0.05}},
            residual_distribution={"temperature_k": {"p05": -0.5, "p50": 0.05, "p95": 0.5}},
            comparisons=[],
            observation_count=12,
            status="EVALUATED",
        )
        session.add(evaluation)
        session.commit()
        model_id, evaluation_id, version_id = model.id, evaluation.id, version.id

    body = {
        "evaluation_id": str(evaluation_id),
        "acceptance_limits": {
            "temperature_k": {
                "mae": {"maximum": 1.0},
                "rmse": {"maximum": 1.5},
                "r2": {"minimum": 0.9},
                "bias": {"minimum": -0.2, "maximum": 0.2},
            }
        },
        "review_note": "Reviewed independent held-out engineering evaluation against the agreed acceptance limits.",
    }
    response = client.post(f"/api/v1/models/{model_id}/validate", headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "VALIDATED"
    staged = client.post(f"/api/v1/models/{model_id}/stage", headers=headers)
    assert staged.status_code == 200
    assert staged.json()["status"] == "STAGING"
    promoted = client.post(f"/api/v1/models/{model_id}/promote", headers=headers)
    assert promoted.status_code == 200
    assert promoted.json()["status"] == "PRODUCTION"

    evaluation_request = client.post(
        f"/api/v1/models/{model_id}/evaluate",
        headers=headers,
        json={"dataset_version_id": str(version_id)},
    )
    assert evaluation_request.status_code == 409
    assert evaluation_request.json()["error"]["code"] == "MODEL_VERSION_IMMUTABLE"


def test_validation_rejects_failed_acceptance_criteria() -> None:
    client, headers, plant, _ = tenant_client()
    with SessionLocal() as session:
        membership = session.query(OrganizationMembership).one()
        membership.role = RoleName.ADMIN
        dataset = Dataset(
            organization_id=membership.organization_id, plant_id=plant.id, name="Test fixture"
        )
        session.add(dataset)
        session.flush()
        version = DatasetVersion(
            organization_id=membership.organization_id,
            dataset_id=dataset.id,
            version=1,
            status="IMPORTED",
            source_filename="fixture.csv",
            checksum_sha256="1" * 64,
            row_count=12,
            measurement_count=12,
            quality_summary={},
        )
        session.add(version)
        session.flush()
        model = ModelVersion(
            organization_id=membership.organization_id,
            name="Candidate",
            version="1",
            model_type="physics_cstr",
            status="EVALUATED",
            feature_schema={},
            target_schema={},
            metrics={},
            operating_envelope={"temperature_k": {"minimum": 400, "maximum": 500}},
        )
        session.add(model)
        session.flush()
        evaluation = ModelEvaluation(
            organization_id=membership.organization_id,
            model_version_id=model.id,
            dataset_version_id=version.id,
            evaluation_type="PHYSICS_ONLY",
            metrics={"temperature_k": {"mae": 3.0}},
            residual_distribution={},
            comparisons=[],
            observation_count=12,
            status="EVALUATED",
        )
        session.add(evaluation)
        session.commit()
        model_id, evaluation_id = model.id, evaluation.id
    response = client.post(
        f"/api/v1/models/{model_id}/validate",
        headers=headers,
        json={
            "evaluation_id": str(evaluation_id),
            "acceptance_limits": {"temperature_k": {"mae": {"maximum": 1.0}}},
            "review_note": "This candidate does not satisfy the specified temperature error acceptance criterion.",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_CRITERIA_NOT_MET"


def test_parameter_set_creation_is_immutable_and_records_complete_bounds() -> None:
    client, headers, _, _ = tenant_client()
    catalog = parameter_catalog()
    response = client.get("/api/v1/physics/parameter-catalog", headers=headers)
    assert response.status_code == 200
    assert response.json()["units"] == "SI"
    request = {
        "name": "reviewed reference parameters",
        "source": "engineering review fixture",
        "description": "Automated API fixture; not plant calibrated.",
        "parameters": {name: item.as_dict() for name, item in catalog.items()},
    }
    created = client.post("/api/v1/physics/parameter-sets", headers=headers, json=request)
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1
    assert created.json()["parameters"]["U_w_m2_k"]["unit"] == "W/(m^2 K)"
    duplicate = client.post("/api/v1/physics/parameter-sets", headers=headers, json=request)
    assert duplicate.status_code == 409
    assert client.get("/api/v1/physics/parameter-sets").status_code == 401
