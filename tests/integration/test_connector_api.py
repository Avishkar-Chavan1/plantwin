"""Connector/tag/mapping/validation-rule API: validation, auditing, read-only invariants, tenancy."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from apps.api.processtwin_api.auth import hash_password
from apps.api.processtwin_api.database import SessionLocal
from apps.api.processtwin_api.main import app
from apps.api.processtwin_api.models import (
    AuditLog,
    ConnectorTag,
    DataSource,
    Organization,
    OrganizationMembership,
    RoleName,
    SourceTagMapping,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.integration.test_historical_datasets import tenant_client
from tests.integration.test_live_readonly_workflow import configure_live_fixture


def _tag_body(plant_id: object, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "plant_id": str(plant_id),
        "plant_tag": "TI_101",
        "canonical_name": "reactor.temperature",
        "engineering_unit": "degC",
        "normalized_unit": "K",
        "minimum_si": 273.15,
        "maximum_si": 573.15,
        "expected_sampling_interval_s": 5,
    }
    body.update(overrides)
    return body


def _second_tenant() -> dict[str, str]:
    with SessionLocal() as session:
        organization = Organization(name="Other Tenant")
        user = User(email="other@example.test", password_hash=hash_password("CorrectHorseBattery1"))
        session.add_all([organization, user])
        session.flush()
        session.add(
            OrganizationMembership(
                organization_id=organization.id, user_id=user.id, role=RoleName.OWNER
            )
        )
        session.commit()
        organization_id = organization.id
    login = TestClient(app).post(
        "/api/v1/auth/login",
        json={"email": "other@example.test", "password": "CorrectHorseBattery1"},
    )
    return {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Organization-ID": str(organization_id),
    }


def test_connector_tag_lifecycle_validation_and_audit() -> None:
    client, headers, plant, _equipment = tenant_client()
    created = client.post("/api/v1/connectors/tags", headers=headers, json=_tag_body(plant.id))
    assert created.status_code == 201, created.text
    assert created.json()["tag"] == "TI_101"
    assert created.json()["normalized_unit"] == "K"

    listed = client.get(f"/api/v1/connectors/tags?plant_id={plant.id}", headers=headers)
    assert [item["tag"] for item in listed.json()["items"]] == ["TI_101"]

    duplicate = client.post("/api/v1/connectors/tags", headers=headers, json=_tag_body(plant.id))
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CONNECTOR_TAG_EXISTS"

    wrong_units = client.post(
        "/api/v1/connectors/tags",
        headers=headers,
        json=_tag_body(plant.id, plant_tag="TI_102", normalized_unit="Pa"),
    )
    assert wrong_units.status_code == 422
    assert wrong_units.json()["error"]["code"] == "INVALID_TAG_UNITS"

    inverted = client.post(
        "/api/v1/connectors/tags",
        headers=headers,
        json=_tag_body(plant.id, plant_tag="TI_103", minimum_si=500, maximum_si=400),
    )
    assert inverted.json()["error"]["code"] == "INVALID_TAG_RANGE"

    wildcard = client.post(
        "/api/v1/connectors/tags", headers=headers, json=_tag_body(plant.id, plant_tag="TI_#")
    )
    assert wildcard.status_code == 422

    unknown_plant = client.post(
        "/api/v1/connectors/tags",
        headers=headers,
        json=_tag_body("00000000-0000-0000-0000-000000000001", plant_tag="TI_104"),
    )
    assert unknown_plant.status_code == 404

    with SessionLocal() as session:
        actions = [row.action for row in session.scalars(select(AuditLog))]
    assert "CONFIGURE_CONNECTOR_TAG" in actions


def test_validation_errors_name_the_field_and_never_echo_submitted_values() -> None:
    client, headers, plant, _equipment = tenant_client()
    response = client.post(
        "/api/v1/connectors/tags",
        headers=headers,
        json={"plant_id": str(plant.id), "plant_tag": "TI_1", "engineering_unit": "SECRET-VALUE-77"},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_REQUEST"
    fields = {detail["field"] for detail in error["details"]}
    assert {"canonical_name", "normalized_unit"} <= fields
    assert "canonical_name" in error["message"]
    assert "SECRET-VALUE-77" not in response.text
    assert error["request_id"]


def test_mapping_bridges_to_the_runtime_gateway_mapping_and_rejects_conflicts() -> None:
    client, headers, plant, sensors, source = configure_live_fixture()
    tag = client.post(
        "/api/v1/connectors/tags",
        headers=headers,
        json=_tag_body(
            plant.id, plant_tag="TI_900", canonical_name="reactor.temperature", normalized_unit="K"
        ),
    ).json()
    mapping_body = {
        "data_source_id": str(source.id),
        "connector_tag_id": tag["id"],
        "sensor_id": str(sensors["reactor_temperature"].id),
        "source_key": "ns=2;s=R101.Temperature",
    }
    created = client.post("/api/v1/connectors/tag-mappings", headers=headers, json=mapping_body)
    assert created.status_code == 201, created.text
    with SessionLocal() as session:
        runtime = session.scalar(
            select(SourceTagMapping).where(
                SourceTagMapping.data_source_id == source.id,
                SourceTagMapping.source_key == "ns=2;s=R101.Temperature",
            )
        )
        assert runtime is not None  # the gateway ingests through this row
        assert runtime.source_unit == "degC"
        assert runtime.canonical_name == "reactor.temperature"
    again = client.post("/api/v1/connectors/tag-mappings", headers=headers, json=mapping_body)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "CONNECTOR_TAG_MAPPING_EXISTS"
    listing = client.get(
        f"/api/v1/connectors/tag-mappings?data_source_id={source.id}", headers=headers
    )
    assert len(listing.json()["items"]) == 1
    status = client.get(f"/api/v1/connectors/{source.id}", headers=headers).json()
    assert status["mappings"] == 6  # five fixture mappings + the new one
    assert status["read_only"] is True

    pressure_tag = client.post(
        "/api/v1/connectors/tags",
        headers=headers,
        json=_tag_body(
            plant.id,
            plant_tag="PI_900",
            canonical_name="reactor.pressure",
            engineering_unit="bar",
            normalized_unit="Pa",
            minimum_si=None,
            maximum_si=None,
        ),
    ).json()
    incompatible = client.post(
        "/api/v1/connectors/tag-mappings",
        headers=headers,
        json={
            **mapping_body,
            "connector_tag_id": pressure_tag["id"],
            "source_key": "ns=2;s=R101.Pressure",
        },
    )
    assert incompatible.status_code == 422
    assert incompatible.json()["error"]["code"] == "INVALID_SOURCE_UNIT"


def test_validation_rules_only_accept_known_types_once_per_tag() -> None:
    client, headers, plant, _equipment = tenant_client()
    tag = client.post("/api/v1/connectors/tags", headers=headers, json=_tag_body(plant.id)).json()
    unknown = client.post(
        "/api/v1/connectors/validation-rules",
        headers=headers,
        json={"connector_tag_id": tag["id"], "rule_type": "RANG"},
    )
    assert unknown.status_code == 422
    created = client.post(
        "/api/v1/connectors/validation-rules",
        headers=headers,
        json={"connector_tag_id": tag["id"], "rule_type": "range", "parameters": {"min": 0}},
    )
    assert created.status_code == 201
    assert created.json()["rule_type"] == "RANGE"
    repeat = client.post(
        "/api/v1/connectors/validation-rules",
        headers=headers,
        json={"connector_tag_id": tag["id"], "rule_type": "RANGE"},
    )
    assert repeat.status_code == 409
    assert len(client.get("/api/v1/connectors/validation-rules", headers=headers).json()["items"]) == 1


def test_connector_creation_is_always_read_only_and_never_accepts_credentials_in_urls() -> None:
    client, headers, plant, _equipment = tenant_client()
    body = {
        "plant_id": str(plant.id),
        "name": "Reactor OPC-UA",
        "source_type": "OPCUA",
        "endpoint": "opc.tcp://opcua.plant.example:4840",
        "stale_after_s": 20,
    }
    created = client.post("/api/v1/connectors", headers=headers, json=body)
    assert created.status_code == 201, created.text
    assert created.json()["read_only"] is True
    assert created.json()["status"] == "DISCONNECTED"
    assert "password" not in created.text.lower()

    for endpoint in (
        "opc.tcp://user:Zq9pw7@opcua.plant.example:4840",
        "opc.tcp://opcua.plant.example:4840?password=Zq9pw7",
        "http://opcua.plant.example",
    ):
        rejected = client.post(
            "/api/v1/connectors", headers=headers, json={**body, "name": "x", "endpoint": endpoint}
        )
        assert rejected.status_code == 422, endpoint
        assert "Zq9pw7" not in rejected.text

    attempt = client.post(
        "/api/v1/connectors", headers=headers, json={**body, "name": "y", "read_only": False}
    )
    assert attempt.status_code == 422  # unknown field: a source cannot be made writable

    source_id = created.json()["id"]
    health = client.get(f"/api/v1/connectors/{source_id}/health", headers=headers)
    assert health.status_code == 200
    assert health.json()["read_only"] is True
    with SessionLocal() as session:
        assert all(row.read_only for row in session.scalars(select(DataSource)))
        actions = [row.action for row in session.scalars(select(AuditLog))]
    assert "CONFIGURE_READ_ONLY_CONNECTOR" in actions


def test_production_transport_policy_for_connector_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers, plant, _equipment = tenant_client()
    monkeypatch.setattr(
        "apps.api.processtwin_api.live_api.get_settings",
        lambda: SimpleNamespace(is_production=True),
    )
    plain_mqtt = client.post(
        "/api/v1/connectors",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "name": "m",
            "source_type": "MQTT",
            "endpoint": "mqtt://broker.example:1883",
        },
    )
    assert plain_mqtt.json()["error"]["code"] == "MQTT_TLS_REQUIRED"
    opcua = client.post(
        "/api/v1/connectors",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "name": "o",
            "source_type": "OPCUA",
            "endpoint": "opc.tcp://opcua.example:4840",
        },
    )
    assert opcua.json()["error"]["code"] == "OPCUA_CERTIFICATE_CONFIGURATION_REQUIRED"
    tls_mqtt = client.post(
        "/api/v1/connectors",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "name": "t",
            "source_type": "MQTT",
            "endpoint": "mqtts://broker.example:8883",
        },
    )
    assert tls_mqtt.status_code == 201


def test_connector_data_is_isolated_between_tenants() -> None:
    client, headers, plant, _equipment = tenant_client()
    tag = client.post("/api/v1/connectors/tags", headers=headers, json=_tag_body(plant.id)).json()
    source = client.post(
        "/api/v1/connectors",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "name": "Tenant A source",
            "source_type": "MQTT",
            "endpoint": "mqtt://broker.example:1883",
        },
    ).json()
    other = _second_tenant()

    assert client.get("/api/v1/connectors", headers=other).json()["items"] == []
    assert client.get("/api/v1/connectors/tags", headers=other).json()["items"] == []
    assert client.get(f"/api/v1/connectors/{source['id']}", headers=other).status_code == 404
    assert client.get(f"/api/v1/connectors/{source['id']}/health", headers=other).status_code == 404
    # Another tenant cannot attach to tenant A's plant, tag or source.
    assert (
        client.post("/api/v1/connectors/tags", headers=other, json=_tag_body(plant.id)).status_code
        == 404
    )
    cross = client.post(
        "/api/v1/connectors/tag-mappings",
        headers=other,
        json={
            "data_source_id": source["id"],
            "connector_tag_id": tag["id"],
            "sensor_id": "00000000-0000-0000-0000-000000000002",
            "source_key": "x",
        },
    )
    assert cross.status_code == 404
    # Tenant A's own X-Organization-ID cannot be borrowed by tenant B's token.
    forged = {**other, "X-Organization-ID": headers["X-Organization-ID"]}
    assert client.get("/api/v1/connectors", headers=forged).status_code == 403
    with SessionLocal() as session:
        assert session.scalar(select(ConnectorTag).where(ConnectorTag.id == UUID(tag["id"])))


def test_viewer_cannot_configure_connectors() -> None:
    client, headers, plant, _equipment = tenant_client()
    with SessionLocal() as session:
        membership = session.query(OrganizationMembership).one()
        membership.role = RoleName.VIEWER
        session.commit()
    denied = client.post("/api/v1/connectors/tags", headers=headers, json=_tag_body(plant.id))
    assert denied.status_code == 403
    assert client.get("/api/v1/connectors/tags", headers=headers).status_code == 200
