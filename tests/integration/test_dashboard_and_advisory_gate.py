"""Dashboard honesty (no fabricated data, stale/mixed detection) and the advisory gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from apps.api.processtwin_api.advisory_gate import evaluate_recommendation_gate
from apps.api.processtwin_api.auth import hash_password
from apps.api.processtwin_api.database import Base, SessionLocal, engine
from apps.api.processtwin_api.main import app
from apps.api.processtwin_api.models import (
    ModelVersion,
    Organization,
    OrganizationMembership,
    PhysicsParameterSet,
    QualityStatusName,
    Recommendation,
    RoleName,
    SensorReading,
    TwinState,
    User,
)
from apps.api.processtwin_api.realtime import LiveIngestionGateway
from connectors.telemetry import TelemetryMessage
from fastapi.testclient import TestClient
from packages.calibration import parameter_catalog
from sqlalchemy import select
from tests.integration.test_live_readonly_workflow import configure_live_fixture

LIVE_VALUES = {
    "feed_flow": (72.0, "m3/h"),
    "feed_temperature": (180.0, "degC"),
    "feed_concentration": (100.0, "mol/m3"),
    "cooling_temperature": (20.0, "degC"),
    "reactor_temperature": (180.0, "degC"),
}


def _ingest(source_id: Any, newest: datetime, samples: int = 10, quality: str = "GOOD") -> None:
    from apps.api.processtwin_api.models import DataSource

    with SessionLocal() as session:
        source = session.get(DataSource, source_id)
        assert source is not None
        gateway = LiveIngestionGateway()
        for index in range(samples):
            stamp = newest - timedelta(seconds=5 * (samples - 1 - index))
            for suffix, (value, unit) in LIVE_VALUES.items():
                jitter = 0.01 * (index % 3) if suffix == "reactor_temperature" else 0.0
                gateway.ingest(
                    session,
                    source,
                    TelemetryMessage(
                        source_key=f"processtwin/test-plant/R-101/{suffix}",
                        value=value + jitter,
                        unit=unit,
                        timestamp=stamp,
                        received_at=stamp,
                        quality=quality,  # type: ignore[arg-type]
                    ),
                )
        session.commit()


def _validated_model(organization_id: Any) -> Any:
    with SessionLocal() as session:
        records = {name: item.as_dict() for name, item in parameter_catalog().items()}
        parameters = PhysicsParameterSet(
            organization_id=organization_id,
            name="gate fixture",
            version=1,
            status="CALIBRATED",
            parameters=records,
            source="test fixture only; not real plant validation",
        )
        session.add(parameters)
        session.flush()
        model = ModelVersion(
            organization_id=organization_id,
            name="Gate fixture model",
            version="1",
            model_type="physics_cstr",
            status="VALIDATED",
            feature_schema={},
            target_schema={},
            metrics={"test_fixture": True},
            physics_parameter_set_id=parameters.id,
            operating_envelope={
                "temperature_k": {"minimum": 443.15, "maximum": 463.15},
                "pressure_pa": {"minimum": 800000, "maximum": 1200000},
                "feed_flow_m3_s": {"minimum": 0.016, "maximum": 0.024},
                "max_energy_w": 1000000,
            },
        )
        session.add(model)
        session.commit()
        return model.id


def _organization_id() -> Any:
    with SessionLocal() as session:
        return session.query(OrganizationMembership).one().organization_id


OPTIMIZE = {"temperature_c": 180, "pressure_bar": 10, "flow_m3_h": 72}


# --------------------------------------------------------------------------- dashboard


def test_dashboard_with_no_equipment_returns_empty_sections_not_invented_values() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        organization = Organization(name="Empty Tenant")
        user = User(email="empty@example.test", password_hash=hash_password("CorrectHorseBattery1"))
        session.add_all([organization, user])
        session.flush()
        session.add(
            OrganizationMembership(
                organization_id=organization.id, user_id=user.id, role=RoleName.ENGINEER
            )
        )
        session.commit()
        organization_id = organization.id
    client = TestClient(app)
    token = client.post(
        "/api/v1/auth/login",
        json={"email": "empty@example.test", "password": "CorrectHorseBattery1"},
    ).json()["access_token"]
    body = client.get(
        "/api/v1/dashboard/summary",
        headers={"Authorization": f"Bearer {token}", "X-Organization-ID": str(organization_id)},
    ).json()
    assert body["source_mode"] == "NO_DATA"
    assert body["plant_health"] == "NO_DATA"
    assert body["equipment"] is None
    assert body["measurements"] == {}
    assert body["twin"] is None
    assert body["connectors"] == {"count": 0, "items": []}
    assert body["alerts"]["open_count"] == 0
    assert body["recommendations"]["pending_review_count"] == 0
    assert body["models"]["active"] is None


def test_dashboard_reports_live_read_only_values_with_provenance_and_freshness() -> None:
    client, headers, _plant, sensors, source = configure_live_fixture()
    _ingest(source.id, datetime.now(UTC) - timedelta(seconds=3))
    body = client.get("/api/v1/dashboard/summary", headers=headers).json()
    assert body["source_mode"] == "LIVE_READ_ONLY"
    assert body["data_state"] == "OK"
    assert body["plant_health"] not in {"NO_DATA", "STALE_DATA", "MIXED_DATA_BLOCKED"}
    reading = body["measurements"]["LIVE_REACTOR_TEMPERATURE"]
    assert reading["source"] == "MQTT"
    assert reading["source_mode"] == "LIVE_READ_ONLY"
    assert reading["stale"] is False
    assert reading["age_s"] < 60
    assert reading["unit"] == "degC"
    assert body["data_quality"]["sensors_with_data"] == 5
    assert body["data_quality"]["stale_tags"] == []
    assert body["connectors"]["count"] == 1
    connector = body["connectors"]["items"][0]
    assert connector["read_only"] is True
    assert connector["tags"] == 5
    assert "LIVE READ-ONLY" in body["safety_notice"]
    assert body["twin"]["stale"] is False
    assert body["twin"]["prediction_status"] == "UNVALIDATED_PHYSICS_REFERENCE"
    assert body["models"]["active"] is None
    assert "unvalidated" in body["models"]["validation_note"]


def test_dashboard_marks_dead_connector_data_stale_and_withholds_estimates() -> None:
    client, headers, _plant, _sensors, source = configure_live_fixture()
    _ingest(source.id, datetime.now(UTC) - timedelta(minutes=30))
    body = client.get("/api/v1/dashboard/summary", headers=headers).json()
    assert body["source_mode"] == "LIVE_READ_ONLY"
    assert body["data_state"] == "STALE"
    assert body["plant_health"] == "STALE_DATA"
    assert len(body["data_quality"]["stale_tags"]) == 5
    assert all(item["stale"] is True for item in body["measurements"].values())
    twin = body["twin"]
    assert twin["stale"] is True
    assert twin["yield"] is None and twin["conversion"] is None
    assert "stale" in twin["withheld_reason"]
    assert body["connectors"]["items"][0]["status"] in {"STALE", "CONNECTED", "DISCONNECTED"}


def test_dashboard_blocks_mixed_simulation_and_live_sources() -> None:
    client, headers, _plant, sensors, source = configure_live_fixture()
    _ingest(source.id, datetime.now(UTC) - timedelta(seconds=3), samples=2)
    with SessionLocal() as session:
        sensor = sensors["feed_flow"]
        session.add(
            SensorReading(
                organization_id=_organization_id(),
                sensor_id=sensor.id,
                timestamp=datetime.now(UTC),
                value=70.0,
                unit="m3/h",
                quality_status=QualityStatusName.GOOD,
                quality_reasons=["SYNTHETIC_GOOD_READING"],
                source="SIMULATED",
            )
        )
        session.commit()
    body = client.get("/api/v1/dashboard/summary", headers=headers).json()
    assert body["source_mode"] == "MIXED_DATA_BLOCKED"
    assert body["measurements"] == {}
    assert body["twin"] is None
    assert "never combined" in body["safety_notice"]


def test_dashboard_labels_simulated_telemetry_as_simulation() -> None:
    client, headers, _plant, sensors, _source = configure_live_fixture()
    with SessionLocal() as session:
        session.add(
            SensorReading(
                organization_id=_organization_id(),
                sensor_id=sensors["reactor_temperature"].id,
                timestamp=datetime.now(UTC),
                value=181.0,
                unit="degC",
                quality_status=QualityStatusName.GOOD,
                quality_reasons=["SYNTHETIC_GOOD_READING"],
                source="SIMULATED",
            )
        )
        session.commit()
    body = client.get("/api/v1/dashboard/summary", headers=headers).json()
    assert body["source_mode"] == "SIMULATION"
    assert "SIMULATION" in body["safety_notice"]
    assert body["measurements"]["LIVE_REACTOR_TEMPERATURE"]["source_mode"] == "SIMULATION"
    # No twin state was synchronized, so no estimated values are shown.
    assert body["twin"] is None
    assert body["plant_health"] == "WAITING_FOR_SYNCHRONIZED_TWIN"


def test_dashboard_is_tenant_scoped() -> None:
    client, headers, _plant, _sensors, source = configure_live_fixture()
    _ingest(source.id, datetime.now(UTC) - timedelta(seconds=3), samples=2)
    with SessionLocal() as session:
        other = Organization(name="Isolated")
        user = User(email="iso@example.test", password_hash=hash_password("CorrectHorseBattery1"))
        session.add_all([other, user])
        session.flush()
        session.add(
            OrganizationMembership(organization_id=other.id, user_id=user.id, role=RoleName.OWNER)
        )
        session.commit()
        other_id = other.id
    token = client.post(
        "/api/v1/auth/login",
        json={"email": "iso@example.test", "password": "CorrectHorseBattery1"},
    ).json()["access_token"]
    body = client.get(
        "/api/v1/dashboard/summary",
        headers={"Authorization": f"Bearer {token}", "X-Organization-ID": str(other_id)},
    ).json()
    assert body["equipment"] is None and body["measurements"] == {}
    assert body["connectors"]["count"] == 0
    assert body["recommendations"]["pending_review_count"] == 0


# --------------------------------------------------------------------------- advisory gate


def test_recommendation_is_issued_with_full_context_when_data_is_fresh() -> None:
    client, headers, _plant, sensors, source = configure_live_fixture()
    _ingest(source.id, datetime.now(UTC) - timedelta(seconds=3))
    model_id = _validated_model(_organization_id())
    equipment_id = sensors["reactor_temperature"].equipment_id
    run = client.post(
        "/api/v1/optimization/runs",
        headers=headers,
        json={"equipment_id": str(equipment_id), "model_version_id": str(model_id), **OPTIMIZE},
    )
    assert run.status_code == 200, run.text
    assert run.json()["recommendation_gate"]["status"] == "RECOMMENDATION_ALLOWED", run.json()["recommendation_gate"]
    assert run.json()["recommendation_gate"]["basis"] == "LIVE_READ_ONLY_STATE"
    assert run.json()["recommendation_id"]

    item = next(
        entry
        for entry in client.get("/api/v1/recommendations", headers=headers).json()["items"]
        if entry["id"] == run.json()["recommendation_id"]
    )
    assert item["baseline"]["basis"] == "LIVE_READ_ONLY_STATE"
    assert any(c.startswith("SUSPECT_INPUTS") for c in item["baseline"]["cautions"])  # constant feed
    assert item["requires_engineer_review"] is True
    assert "requires engineer review" in item["advisory"]
    assert item["reason"]
    assert item["confidence"]
    assert item["timestamp"]
    assert item["uncertainty_quantified"] is False
    assert item["validation"] == {"model_status": "VALIDATED", "real_plant_validation": "NOT_ESTABLISHED"}
    assert item["source"]["kind"] == "MODEL_SCENARIO"
    assert item["source"]["model"]["name"] == "Gate fixture model"
    assert item["operating_constraints"]["bounds"]["temperature_k"]
    assert item["allowed_decisions"] == ["REVIEWED", "ACCEPTED", "REJECTED", "EXPIRED"]

    # Every human decision is audited; EXPIRED is a final state.
    expired = client.post(
        f"/api/v1/recommendations/{item['id']}/review",
        headers=headers,
        json={"decision": "EXPIRED", "comment": "Operating conditions changed; withdrawing advice."},
    )
    assert expired.status_code == 200
    assert expired.json()["recommendation"]["status"] == "EXPIRED"
    assert expired.json()["recommendation"]["allowed_decisions"] == []
    final = client.post(
        f"/api/v1/recommendations/{item['id']}/review",
        headers=headers,
        json={"decision": "ACCEPTED", "comment": "Trying to accept after expiry."},
    )
    assert final.status_code == 409
    audit = client.get("/api/v1/audit-log", headers=headers).json()["items"]
    assert any(entry["action"] == "RECOMMENDATION_EXPIRED" for entry in audit)
    run_audit = next(entry for entry in audit if entry["action"] == "RUN_OPTIMIZATION")
    assert run_audit["metadata"]["recommendation_issued"] is True


def test_no_recommendation_when_inputs_are_stale() -> None:
    client, headers, _plant, sensors, source = configure_live_fixture()
    _ingest(source.id, datetime.now(UTC) - timedelta(minutes=30))
    model_id = _validated_model(_organization_id())
    run = client.post(
        "/api/v1/optimization/runs",
        headers=headers,
        json={
            "equipment_id": str(sensors["reactor_temperature"].equipment_id),
            "model_version_id": str(model_id),
            **OPTIMIZE,
        },
    )
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["recommendation_id"] is None
    assert body["simulation_id"] is None
    assert body["recommendation_gate"]["status"] == "NO_RECOMMENDATION"
    assert any(reason.startswith("STALE_DATA") for reason in body["recommendation_gate"]["reasons"])
    assert body["headline"].startswith("NO RECOMMENDATION")
    with SessionLocal() as session:
        assert session.scalar(select(Recommendation)) is None
    audit = client.get("/api/v1/audit-log", headers=headers).json()["items"]
    run_audit = next(entry for entry in audit if entry["action"] == "RUN_OPTIMIZATION")
    assert run_audit["metadata"]["recommendation_issued"] is False
    assert run_audit["metadata"]["withheld_reasons"]


def test_no_recommendation_when_source_quality_is_bad() -> None:
    client, headers, _plant, sensors, source = configure_live_fixture()
    _ingest(source.id, datetime.now(UTC) - timedelta(seconds=3), quality="BAD")
    model_id = _validated_model(_organization_id())
    # Bad-quality samples are retained but excluded from the twin, so no twin can be built.
    with SessionLocal() as session:
        assert session.scalar(select(TwinState)) is None
    run = client.post(
        "/api/v1/optimization/runs",
        headers=headers,
        json={
            "equipment_id": str(sensors["reactor_temperature"].equipment_id),
            "model_version_id": str(model_id),
            **OPTIMIZE,
        },
    ).json()
    # No usable plant state: the result is explicitly a user-supplied scenario, never "live".
    assert run["recommendation_gate"]["basis"] == "USER_SUPPLIED_SCENARIO"
    dashboard = client.get("/api/v1/dashboard/summary", headers=headers).json()
    assert dashboard["measurements"] == {}
    assert dashboard["source_mode"] == "NO_DATA"


def test_no_recommendation_when_operating_point_is_outside_validated_envelope() -> None:
    client, headers, _plant, sensors, _source = configure_live_fixture()
    model_id = _validated_model(_organization_id())
    run = client.post(
        "/api/v1/optimization/runs",
        headers=headers,
        json={
            "equipment_id": str(sensors["reactor_temperature"].equipment_id),
            "model_version_id": str(model_id),
            "temperature_c": 250,
            "pressure_bar": 10,
            "flow_m3_h": 72,
        },
    )
    body = run.json()
    assert run.status_code in {200, 422}, run.text
    if run.status_code == 200:
        assert body["recommendation_id"] is None
        assert any(
            reason.startswith("OUTSIDE_VALIDATED_RANGE")
            for reason in body["recommendation_gate"]["reasons"]
        )


def test_optimization_without_a_validated_model_is_refused() -> None:
    client, headers, _plant, sensors, _source = configure_live_fixture()
    run = client.post(
        "/api/v1/optimization/runs",
        headers=headers,
        json={"equipment_id": str(sensors["reactor_temperature"].equipment_id), **OPTIMIZE},
    )
    assert run.status_code == 422
    assert run.json()["error"]["code"] == "VALIDATED_MODEL_REQUIRED"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("WAITING_FOR_REQUIRED_INPUTS", "WAITING_FOR_REQUIRED_INPUTS"),
        ("BLOCKED_MIXED_SOURCES", "BLOCKED_MIXED_SOURCES"),
        ("PREDICTION_ERROR", "PREDICTION_ERROR"),
        ("OUTSIDE_VALIDATED_MODEL_RANGE", "OUTSIDE_VALIDATED_RANGE"),
    ],
)
def test_gate_blocks_on_unusable_twin_states(status: str, expected: str) -> None:
    client, _headers, _plant, sensors, source = configure_live_fixture()
    _ingest(source.id, datetime.now(UTC) - timedelta(seconds=3), samples=2)
    with SessionLocal() as session:
        states = list(session.scalars(select(TwinState)))
        assert states
        for state in states:  # synchronisation writes several states per tick
            state.prediction_status = status
        session.commit()
        result = evaluate_recommendation_gate(
            session,
            _organization_id(),
            sensors["reactor_temperature"].equipment_id,
            inside_validated_envelope=True,
        )
    assert result.allowed is False
    assert any(reason.startswith(expected) for reason in result.reasons), result.reasons


def test_gate_blocks_on_drift_low_quality_and_high_uncertainty() -> None:
    client, _headers, _plant, sensors, source = configure_live_fixture()
    _ingest(source.id, datetime.now(UTC) - timedelta(seconds=3), samples=2)
    with SessionLocal() as session:
        states = list(session.scalars(select(TwinState)))
        assert states
        for state in states:
            state.health_factors = {**state.health_factors, "model_drift": {"status": "DRIFT_DETECTED"}}
            state.data_quality = {"sensor_count": 5, "good_count": 0}
            state.uncertainty = {"yield_fraction": 0.4}
        session.commit()
        result = evaluate_recommendation_gate(
            session,
            _organization_id(),
            sensors["reactor_temperature"].equipment_id,
            inside_validated_envelope=True,
        )
    prefixes = {reason.split(":")[0] for reason in result.reasons}
    assert {"MODEL_DRIFT", "BAD_DATA_QUALITY", "UNCERTAINTY_TOO_HIGH"} <= prefixes
    assert result.as_dict()["status"] == "NO_RECOMMENDATION"

def test_connector_lists_report_recent_quality_and_last_telemetry() -> None:
    client, headers, _plant, _sensors, source = configure_live_fixture()
    _ingest(source.id, datetime.now(UTC) - timedelta(seconds=3), samples=3)
    for path in ("/api/v1/data-sources", "/api/v1/connectors"):
        item = client.get(path, headers=headers).json()["items"][0]
        assert item["read_only"] is True
        assert item["mappings"] == 5
        assert item["last_message_at"] is not None
        assert item["last_success_at"] is not None
        quality = item["recent_quality"]
        assert sum(quality.values()) == 15  # 3 samples x 5 mapped sensors, all retained
        assert set(quality) <= {"GOOD", "SUSPECT", "BAD", "MISSING"}
        assert item["status"] in {"CONNECTED", "STALE"}
        assert "password" not in str(item).lower()
