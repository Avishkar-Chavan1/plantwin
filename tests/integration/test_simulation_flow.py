"""Simulation: input -> API -> model -> result -> database -> retrievable result."""

from __future__ import annotations

import pytest
from apps.api.processtwin_api.database import SessionLocal
from apps.api.processtwin_api.models import AuditLog, SensorReading, Simulation
from sqlalchemy import func, select
from tests.integration.test_historical_datasets import tenant_client


def _run(client, headers, equipment, **overrides):  # type: ignore[no-untyped-def]
    body = {
        "equipment_id": str(equipment.id),
        "temperature_c": 180,
        "pressure_bar": 10,
        "flow_m3_h": 72,
        **overrides,
    }
    return client.post("/api/v1/simulations", headers=headers, json=body)


def test_changing_operating_conditions_changes_the_model_result_and_trajectory() -> None:
    client, headers, _plant, equipment = tenant_client()
    low = _run(client, headers, equipment, temperature_c=170)
    high = _run(client, headers, equipment, temperature_c=210)
    assert low.status_code == 200 and high.status_code == 200, (low.text, high.text)
    low_body, high_body = low.json(), high.json()
    assert low_body["id"] != high_body["id"]
    assert low_body["scenario"]["temperature_c"] != high_body["scenario"]["temperature_c"]
    assert low_body["scenario"]["conversion_pct"] != high_body["scenario"]["conversion_pct"]
    assert low_body["trajectory"] != high_body["trajectory"]
    assert len(low_body["trajectory"]) == 121
    # The trajectory must start at the baseline steady state and end at the scenario result.
    assert low_body["trajectory"][0]["time_s"] == 0.0
    assert low_body["trajectory"][-1]["temperature_c"] == pytest.approx(
        low_body["scenario"]["temperature_c"], abs=1.0
    )
    # Same inputs -> same outputs (deterministic model, no frontend/random movement).
    again = _run(client, headers, equipment, temperature_c=170).json()
    assert again["trajectory"] == low_body["trajectory"]
    assert again["scenario"] == low_body["scenario"]


def test_results_are_labelled_as_simulated_and_persisted_for_retrieval_and_audit() -> None:
    client, headers, _plant, equipment = tenant_client()
    body = _run(client, headers, equipment, temperature_c=185).json()
    assert body["mode"] == "SIMULATION"
    assert body["source"] == "SIMULATED"
    assert "SIMULATION MODE" in body["warnings"][0]
    assert body["model_validity"] == "UNVALIDATED_REFERENCE"
    assert body["uncertainty"]["status"] == "UNAVAILABLE"
    assert "No actual plant setting has been modified" in body["advisory"]

    stored = client.get(f"/api/v1/simulations/{body['id']}", headers=headers)
    assert stored.status_code == 200
    assert stored.json()["inputs"]["temperature_c"] == 185
    assert stored.json()["results"]["scenario"] == body["scenario"]
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Simulation)) == 1
        assert any(row.action == "RUN_SIMULATION" for row in session.scalars(select(AuditLog)))
        # A simulation must never be written into telemetry where it could pass as plant data.
        assert session.scalar(select(func.count()).select_from(SensorReading)) == 0


def test_operating_point_outside_the_validated_range_is_called_out() -> None:
    client, headers, _plant, equipment = tenant_client()
    body = _run(client, headers, equipment, temperature_c=260).json()
    assert body["constraint_violations"] == ["Outside validated model range."]
    assert any("OUTSIDE VALIDATED MODEL RANGE" in warning for warning in body["warnings"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"temperature_c": 50},
        {"temperature_c": 400},
        {"pressure_bar": 0},
        {"flow_m3_h": -1},
        {"duration_s": 0},
        {"duration_s": 10_000_000},
        {"temperature_c": "hot"},
    ],
)
def test_invalid_inputs_are_rejected_with_the_offending_field(overrides: dict[str, object]) -> None:
    client, headers, _plant, equipment = tenant_client()
    response = _run(client, headers, equipment, **overrides)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert next(iter(overrides)) in error["message"]


def test_unknown_equipment_and_unvalidated_model_are_rejected() -> None:
    client, headers, _plant, equipment = tenant_client()
    missing = client.post(
        "/api/v1/simulations",
        headers=headers,
        json={
            "equipment_id": "00000000-0000-0000-0000-000000000009",
            "temperature_c": 180,
            "pressure_bar": 10,
            "flow_m3_h": 72,
        },
    )
    assert missing.status_code == 404
    unknown_model = _run(
        client, headers, equipment, model_version_id="00000000-0000-0000-0000-000000000009"
    )
    assert unknown_model.status_code == 422
    assert unknown_model.json()["error"]["code"] == "VALIDATED_MODEL_REQUIRED"
