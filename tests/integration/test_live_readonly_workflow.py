from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from apps.api.processtwin_api.database import SessionLocal
from apps.api.processtwin_api.models import (
    DataSource,
    ModelVersion,
    OrganizationMembership,
    PhysicsParameterSet,
    Sensor,
    TwinState,
)
from apps.api.processtwin_api.realtime import LiveIngestionGateway
from connectors.mqtt.adapter import MqttReadingAdapter
from connectors.telemetry import SourceHealthMonitor, TelemetryMessage
from fastapi.testclient import TestClient
from packages.calibration import parameter_catalog
from sqlalchemy import select
from tests.integration.test_historical_datasets import tenant_client

REQUIRED_TAGS = (
    ("feed_flow", "reactor.feed_flow", "flow", "m3/h", "m3/h"),
    ("feed_temperature", "reactor.feed_temperature", "temperature", "degC", "degC"),
    ("feed_concentration", "reactor.feed_concentration", "concentration", "mol/m3", "mol/m3"),
    ("cooling_temperature", "reactor.cooling_temperature", "temperature", "degC", "degC"),
    ("reactor_temperature", "reactor.temperature", "temperature", "degC", "degC"),
)


def configure_live_fixture() -> tuple[
    TestClient, dict[str, str], object, dict[str, Sensor], DataSource
]:
    client, headers, plant, equipment = tenant_client()
    sensors: dict[str, Sensor] = {}
    with SessionLocal() as session:
        organization_id = session.query(OrganizationMembership).one().organization_id
        for suffix, canonical, measurement_type, unit, _source_unit in REQUIRED_TAGS:
            sensor = Sensor(
                organization_id=organization_id,
                equipment_id=equipment.id,
                name=canonical.rsplit(".", maxsplit=1)[-1],
                tag=f"LIVE_{suffix.upper()}",
                unit=unit,
                measurement_type=measurement_type,
                min_valid_value=-1_000_000,
                max_valid_value=1_000_000,
            )
            session.add(sensor)
            sensors[suffix] = sensor
        session.commit()
        sensor_ids = {key: value.id for key, value in sensors.items()}
    mapping_inputs = []
    source_keys: dict[str, str] = {}
    for suffix, canonical, _kind, _unit, source_unit in REQUIRED_TAGS:
        key = f"processtwin/test-plant/R-101/{suffix}"
        source_keys[suffix] = key
        mapping_inputs.append(
            {
                "source_key": key,
                "sensor_id": str(sensor_ids[suffix]),
                "canonical_name": canonical,
                "source_unit": source_unit,
            }
        )
    created = client.post(
        "/api/v1/data-sources",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "name": "Test MQTT read-only",
            "source_type": "MQTT",
            "endpoint": "mqtt://localhost:1883",
            "stale_after_s": 60,
            "mappings": mapping_inputs,
        },
    )
    assert created.status_code == 201, created.text
    with SessionLocal() as session:
        # HTTP JSON serializes UUIDs as strings; SQLAlchemy's UUID column expects a UUID object.
        source = session.get(DataSource, UUID(created.json()["id"]))
        assert source is not None
        session.expunge(source)
        loaded_sensors: dict[str, Sensor] = {}
        for key, sensor_id in sensor_ids.items():
            sensor = session.get(Sensor, sensor_id)
            assert sensor is not None
            session.expunge(sensor)
            loaded_sensors[key] = sensor
    sensors = loaded_sensors
    return client, headers, plant, sensors, source


def test_read_only_source_ingestion_twin_anomaly_optimization_review_audit() -> None:
    client, headers, plant, sensors, detached_source = configure_live_fixture()
    with SessionLocal() as session:
        source = session.get(DataSource, detached_source.id)
        assert source is not None and source.read_only
        gateway = LiveIngestionGateway()
        base = datetime.now(UTC) - timedelta(minutes=2)
        last_result: dict[str, object] = {}
        for index in range(12):
            stamp = base + timedelta(seconds=index * 5)
            values = {
                "feed_flow": (72.0, "m3/h"),
                "feed_temperature": (180.0, "degC"),
                "feed_concentration": (100.0, "mol/m3"),
                "cooling_temperature": (20.0, "degC"),
                "reactor_temperature": (180.0 + (0.1 if index % 2 else -0.1), "degC"),
            }
            for suffix, (value, unit) in values.items():
                message = TelemetryMessage(
                    source_key=f"processtwin/test-plant/R-101/{suffix}",
                    value=value,
                    unit=unit,
                    timestamp=stamp,
                    received_at=stamp + timedelta(milliseconds=40),
                )
                last_result = gateway.ingest(session, source, message)
        # Instrument deviation is retained and labelled POTENTIAL ANOMALY, never equipment failure.
        spike_time = base + timedelta(seconds=65)
        last_result = gateway.ingest(
            session,
            source,
            TelemetryMessage(
                source_key="processtwin/test-plant/R-101/reactor_temperature",
                value=240.0,
                unit="degC",
                timestamp=spike_time,
                received_at=spike_time + timedelta(milliseconds=35),
            ),
        )
        organization_id = source.organization_id
        equipment_id = sensors["reactor_temperature"].equipment_id
        assert last_result["accepted"] is True
        session.commit()
        twin = session.scalar(
            select(TwinState)
            .where(
                TwinState.organization_id == organization_id,
                TwinState.equipment_id == equipment_id,
            )
            .order_by(TwinState.timestamp.desc())
        )
        assert twin is not None
        assert twin.source_mode == "LIVE_READ_ONLY"
        assert twin.prediction_status == "UNVALIDATED_PHYSICS_REFERENCE"
        assert twin.state["physics_residual_temperature_k"] is not None
        assert twin.health_factors

    source_status = client.get(f"/api/v1/data-sources/{detached_source.id}", headers=headers)
    assert source_status.status_code == 200
    assert source_status.json()["read_only"] is True
    assert source_status.json()["last_success_at"] is not None
    assert source_status.json()["status"] in {"CONNECTED", "STALE"}
    twin_response = client.get(f"/api/v1/digital-twins/{equipment_id}/latest", headers=headers)
    assert twin_response.status_code == 200
    assert twin_response.json()["mode"] == "LIVE_READ_ONLY"
    anomaly_response = client.get("/api/v1/anomalies", headers=headers)
    assert anomaly_response.status_code == 200
    anomaly = next(
        item
        for item in anomaly_response.json()["items"]
        if item["equipment_id"] == str(equipment_id)
    )
    assert anomaly["label"] == "POTENTIAL ANOMALY"
    assert anomaly["potential_contributing_variables"]
    assert anomaly["causality_claimed"] is False

    # Register an explicitly VALIDATED fixture model to prove optimization refuses the unvalidated default.
    with SessionLocal() as session:
        org = session.query(OrganizationMembership).one().organization_id
        records = {name: item.as_dict() for name, item in parameter_catalog().items()}
        parameters = PhysicsParameterSet(
            organization_id=org,
            name="live workflow validated fixture",
            version=1,
            status="CALIBRATED",
            parameters=records,
            source="test fixture only; not real plant validation",
        )
        session.add(parameters)
        session.flush()
        model = ModelVersion(
            organization_id=org,
            name="Validated CSTR workflow fixture",
            version="test-1",
            model_type="physics_cstr",
            status="VALIDATED",
            feature_schema={"units": "SI"},
            target_schema={"outputs": ["temperature_k"]},
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
        model_id = model.id
    simulation = client.post(
        "/api/v1/simulations",
        headers=headers,
        json={
            "equipment_id": str(equipment_id),
            "model_version_id": str(model_id),
            "temperature_c": 180,
            "pressure_bar": 10,
            "flow_m3_h": 72,
        },
    )
    assert simulation.status_code == 200, simulation.text
    assert simulation.json()["mode"] == "SIMULATION"
    assert simulation.json()["model_version_id"] == str(model_id)
    assert set(simulation.json()["baseline"]) >= {
        "temperature_c",
        "pressure_bar",
        "conversion_pct",
        "yield_pct",
        "selectivity_pct",
        "energy_proxy_kw",
    }
    optimization = client.post(
        "/api/v1/optimization/runs",
        headers=headers,
        json={
            "equipment_id": str(equipment_id),
            "model_version_id": str(model_id),
            "temperature_c": 180,
            "pressure_bar": 10,
            "flow_m3_h": 72,
        },
    )
    assert optimization.status_code == 200, optimization.text
    assert optimization.json()["constraints"]["status"] == "PASS"
    assert optimization.json()["recommendation_id"]
    recommendation_id = optimization.json()["recommendation_id"]
    assert optimization.json()["model_validity"] == "VALIDATED_MODEL_RANGE"
    review = client.post(
        f"/api/v1/recommendations/{recommendation_id}/review",
        headers=headers,
        json={
            "decision": "REVIEWED",
            "comment": "Reviewed fixture recommendation and confirmed constraints are recorded.",
        },
    )
    assert review.status_code == 200, review.text
    accepted = client.post(
        f"/api/v1/recommendations/{recommendation_id}/review",
        headers=headers,
        json={
            "decision": "ACCEPTED",
            "comment": "Accepted for further human procedure review; no control action is initiated.",
        },
    )
    assert accepted.status_code == 200
    reviews = client.get(f"/api/v1/recommendations/{recommendation_id}/reviews", headers=headers)
    assert [item["decision"] for item in reviews.json()["items"]] == ["REVIEWED", "ACCEPTED"]
    audit = client.get("/api/v1/audit-log", headers=headers)
    assert any(item["action"] == "RECOMMENDATION_ACCEPTED" for item in audit.json()["items"])


def test_gateway_rejects_mismapped_and_duplicate_live_samples() -> None:
    _client, _headers, _plant, _sensors, source = configure_live_fixture()
    gateway = LiveIngestionGateway()
    stamp = datetime.now(UTC)
    with SessionLocal() as session:
        stored = session.get(DataSource, source.id)
        assert stored is not None
        unknown = TelemetryMessage(source_key="opc:unmapped", value=1, unit="Pa", timestamp=stamp)
        with pytest.raises(ValueError, match="not mapped"):
            gateway.ingest(session, stored, unknown)
        topic = "processtwin/test-plant/R-101/feed_flow"
        sample = TelemetryMessage(source_key=topic, value=72, unit="m3/h", timestamp=stamp)
        first = gateway.ingest(session, stored, sample)
        assert first["accepted"] is True
        session.flush()
        duplicate = gateway.ingest(session, stored, sample)
        assert duplicate["accepted"] is False
        assert duplicate["quality_status"] == "BAD"


def test_mode_classifier_never_calls_historical_or_simulated_values_live() -> None:
    from apps.api.processtwin_api.realtime import source_mode

    assert source_mode("SIMULATED") == "SIMULATION"
    assert source_mode("CSV") == "HISTORICAL"
    assert source_mode("MQTT") == "LIVE_READ_ONLY"
    assert source_mode("OPCUA") == "LIVE_READ_ONLY"


def test_mqtt_timestamp_falls_back_to_receive_time_with_explicit_provenance() -> None:
    received = datetime(2026, 9, 29, tzinfo=UTC)
    message = MqttReadingAdapter().parse_message(
        "processtwin/org/plant/unit/sensor",
        b'{"value":12.5,"unit":"bar"}',
        received,
    )
    assert message.timestamp == received
    assert message.timestamp_source == "RECEIVED"


def test_health_monitor_tracks_rate_latency_freshness_and_error_recovery() -> None:
    monitor = SourceHealthMonitor(stale_after_s=5, rate_window_s=10)
    now = datetime.now(UTC)
    monitor.set_connected(True)
    message = TelemetryMessage(
        source_key="test",
        timestamp=now - timedelta(milliseconds=250),
        received_at=now,
        value=1,
        unit="Pa",
    )
    monitor.record_message(message, successful=True)
    snapshot = monitor.snapshot(now)
    assert snapshot.status == "CONNECTED"
    assert snapshot.message_rate_per_minute == pytest.approx(6.0)
    assert snapshot.latency_ms == pytest.approx(250.0)
    monitor.record_error("temporary disconnect")
    assert monitor.snapshot(now).status == "ERROR"
    monitor.record_message(message, successful=True)
    assert monitor.snapshot(now).status == "CONNECTED"
    assert monitor.snapshot(now + timedelta(seconds=6)).status == "STALE"
