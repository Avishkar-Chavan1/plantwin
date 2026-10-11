from __future__ import annotations

from datetime import UTC, datetime

from apps.api.processtwin_api.auth import hash_password
from apps.api.processtwin_api.database import Base, SessionLocal, engine
from apps.api.processtwin_api.models import (
    Equipment,
    Organization,
    OrganizationMembership,
    Plant,
    QualityEvent,
    RoleName,
    Sensor,
    User,
)
from fastapi.testclient import TestClient


def setup_tenant() -> tuple[TestClient, dict[str, str], Sensor]:
    # Import app HERE after test env is patched by conftest
    from apps.api.processtwin_api.main import app

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        organization = Organization(name="API Test Organization")
        user = User(
            email="tester@example.test", password_hash=hash_password("CorrectHorseBattery1")
        )
        session.add_all([organization, user])
        session.flush()
        session.add(
            OrganizationMembership(
                organization_id=organization.id, user_id=user.id, role=RoleName.ENGINEER
            )
        )
        plant = Plant(organization_id=organization.id, name="Test Plant")
        session.add(plant)
        session.flush()
        equipment = Equipment(
            organization_id=organization.id,
            plant_id=plant.id,
            tag="R-101",
            name="Test CSTR",
            equipment_type="CSTR",
        )
        session.add(equipment)
        session.flush()
        sensor = Sensor(
            organization_id=organization.id,
            equipment_id=equipment.id,
            name="Temperature",
            tag="REACTOR_TEMPERATURE",
            unit="degC",
            measurement_type="temperature",
            min_valid_value=100,
            max_valid_value=250,
        )
        session.add(sensor)
        session.commit()
        sensor_id = sensor.id
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "tester@example.test", "password": "CorrectHorseBattery1"},
    )
    assert login.status_code == 200
    body = login.json()
    headers = {
        "Authorization": f"Bearer {body['access_token']}",
        "X-Organization-ID": body["organizations"][0]["id"],
    }
    with SessionLocal() as session:
        sensor = session.get(Sensor, sensor_id)
        assert sensor is not None
        session.expunge(sensor)
    return client, headers, sensor


def test_authenticated_tenant_can_list_only_its_plants() -> None:
    client, headers, _ = setup_tenant()
    response = client.get("/api/v1/plants", headers=headers)
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Test Plant"


def test_cross_tenant_header_is_forbidden() -> None:
    client, headers, _ = setup_tenant()
    headers["X-Organization-ID"] = "00000000-0000-0000-0000-000000000001"
    response = client.get("/api/v1/plants", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "HTTP_ERROR"


def test_login_cors_preflight_allows_only_the_configured_origin() -> None:
    from apps.api.processtwin_api.main import app

    client = TestClient(app)
    allowed = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in allowed.headers["access-control-allow-methods"]
    assert "content-type" in allowed.headers["access-control-allow-headers"].lower()

    rejected = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://unconfigured.example.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


def test_login_normalizes_email_and_returns_auth_tokens() -> None:
    client, _, _ = setup_tenant()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": " TESTER@EXAMPLE.TEST ", "password": "CorrectHorseBattery1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["organizations"]


def test_invalid_sensor_value_is_rejected_but_quality_event_is_audited() -> None:
    client, headers, sensor = setup_tenant()
    response = client.post(
        "/api/v1/ingestion/readings",
        headers=headers,
        json={
            "sensor_id": str(sensor.id),
            "timestamp": datetime.now(UTC).isoformat(),
            "value": 900,
            "unit": "degC",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_SENSOR_READING"
    with SessionLocal() as session:
        assert session.query(QualityEvent).count() == 1


def test_ingestion_retains_original_value_and_si_normalization() -> None:
    client, headers, sensor = setup_tenant()
    response = client.post(
        "/api/v1/ingestion/readings",
        headers=headers,
        json={
            "sensor_id": str(sensor.id),
            "timestamp": datetime.now(UTC).isoformat(),
            "value": 453.15,
            "unit": "K",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["original_value"] == 453.15
    assert body["original_unit"] == "K"
    assert body["normalized_value"] == 453.15
    assert body["normalized_unit"] == "K"
    assert body["quality_reasons"]


def test_unauthenticated_requests_are_rejected() -> None:
    client, _, _ = setup_tenant()
    assert client.get("/api/v1/plants").status_code == 401
