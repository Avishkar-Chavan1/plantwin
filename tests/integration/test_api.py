from __future__ import annotations

from datetime import UTC, datetime

import pytest
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
        loaded_sensor = session.get(Sensor, sensor_id)
        assert loaded_sensor is not None
        session.expunge(loaded_sensor)
    return client, headers, loaded_sensor


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


def test_production_disables_docs_but_keeps_login_route_and_configured_cors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.api.processtwin_api import main
    from apps.api.processtwin_api.config import Settings

    production_settings = Settings(
        environment="production",
        database_url="postgresql+psycopg://processtwin:synthetic-password@localhost/processtwin",
        jwt_secret="synthetic-production-secret-not-for-deployment-0123456789",
        metrics_token="synthetic-production-metrics-not-for-deployment-0123456789",
        cors_origins="https://plantwin.vercel.app",
        demo_email=None,
        demo_password=None,
    )
    monkeypatch.setattr(main, "get_settings", lambda: production_settings)
    production_app = main.create_app()

    assert production_app.docs_url is None
    assert production_app.redoc_url is None
    assert any(
        getattr(route, "path", None) == "/api/v1/auth/login"
        and "POST" in getattr(route, "methods", set())
        for route in main.app.routes
    )

    client = TestClient(production_app)
    allowed = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://plantwin.vercel.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://plantwin.vercel.app"
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
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["organizations"]


def test_login_invalid_credentials_returns_401_with_cors() -> None:
    client, _, _ = setup_tenant()
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "tester@example.test", "password": "IncorrectPassword"},
        headers={"Origin": "http://localhost:3000"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_login_unexpected_error_is_logged_safely_and_keeps_cors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from apps.api.processtwin_api import main

    client, _, _ = setup_tenant()
    exception_text = "synthetic error detail that must not enter logs"

    def fail_password_check(_password: str, _password_hash: str) -> bool:
        raise RuntimeError(exception_text)

    monkeypatch.setattr(main, "verify_password", fail_password_check)
    caplog.set_level("ERROR", logger="processtwin.api")
    request_id = "diagnostic-request-123"
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "tester@example.test", "password": "SyntheticNotASecret"},
        headers={
            "Origin": "http://localhost:3000",
            "X-Request-ID": request_id,
        },
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["x-request-id"] == request_id
    assert exception_text not in caplog.text
    error_record = next(
        record
        for record in caplog.records
        if getattr(record, "request_id", None) == request_id
    )
    logged_fields = vars(error_record)
    assert logged_fields["exception_type"] == "RuntimeError"
    assert logged_fields["exception_chain"][0] == "RuntimeError"
    assert any(
        frame["function"] == "fail_password_check"
        for frame in logged_fields["exception_frames"]
    )


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
