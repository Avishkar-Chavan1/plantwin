"""Connector, dashboard and upload paths under *enforced* PostgreSQL row-level security.

Runs only against a real PostgreSQL target with a non-owner application role (see
``test_postgres_rls.py`` for the setup contract). Complements the SQLite suite by proving that
every statement in these flows runs with the tenant GUC set, including statements issued after a
mid-request ``commit()`` (which clears transaction-local settings).
"""

from __future__ import annotations

import io
import json
import os
from typing import Any
from uuid import uuid4

import pytest

APP_URL = os.environ.get("TEST_DATABASE_URL", "")
ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", APP_URL)
IS_POSTGRES = APP_URL.startswith(("postgresql://", "postgres://", "postgresql+psycopg://"))
PASSWORD = "RlsConnectors!123"

pytestmark = pytest.mark.skipif(
    not IS_POSTGRES, reason="requires TEST_DATABASE_URL pointing at a PostgreSQL server"
)


@pytest.fixture(scope="module")
def tenants() -> dict[str, Any]:
    from apps.api.processtwin_api.auth import hash_password
    from apps.api.processtwin_api.models import (
        Equipment,
        Organization,
        OrganizationMembership,
        Plant,
        RoleName,
        Sensor,
        User,
    )
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    suffix = uuid4().hex[:10]
    engine = create_engine(ADMIN_URL)
    try:
        with Session(engine) as session:
            result: dict[str, Any] = {}
            for label in ("a", "b"):
                organization = Organization(name=f"Connector RLS {label} {suffix}")
                user = User(email=f"conn-{label}-{suffix}@example.test", password_hash=hash_password(PASSWORD))
                session.add_all([organization, user])
                session.flush()
                session.add(OrganizationMembership(organization_id=organization.id, user_id=user.id, role=RoleName.ENGINEER))
                plant = Plant(organization_id=organization.id, name=f"Plant {label} {suffix}")
                session.add(plant)
                session.flush()
                equipment = Equipment(organization_id=organization.id, plant_id=plant.id, tag=f"R-{label}", name="Reactor", equipment_type="CSTR")
                session.add(equipment)
                session.flush()
                sensor = Sensor(
                    organization_id=organization.id, equipment_id=equipment.id, name="Temperature",
                    tag=f"TEMP_{label}_{suffix}", unit="degC", measurement_type="temperature",
                    min_valid_value=-100, max_valid_value=1000,
                )
                session.add(sensor)
                session.flush()
                result[label] = {
                    "email": user.email, "org": str(organization.id), "plant": str(plant.id),
                    "sensor": str(sensor.id), "equipment": str(equipment.id),
                }
            session.commit()
            return result
    finally:
        engine.dispose()


def _headers(client: Any, tenant: dict[str, str]) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"email": tenant["email"], "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}", "X-Organization-ID": tenant["org"]}


def test_connector_dashboard_and_upload_flows_work_and_isolate_under_rls(tenants: dict[str, Any]) -> None:
    from apps.api.processtwin_api.main import app
    from fastapi.testclient import TestClient

    a, b = tenants["a"], tenants["b"]
    with TestClient(app) as client:
        ha, hb = _headers(client, a), _headers(client, b)

        tag = client.post(
            "/api/v1/connectors/tags", headers=ha,
            json={"plant_id": a["plant"], "plant_tag": "TI_RLS", "canonical_name": "reactor.temperature",
                  "engineering_unit": "degC", "normalized_unit": "K"},
        )
        assert tag.status_code == 201, tag.text
        source = client.post(
            "/api/v1/connectors", headers=ha,
            json={"plant_id": a["plant"], "name": "RLS OPC-UA", "source_type": "OPCUA", "endpoint": "opc.tcp://opcua.example:4840"},
        )
        assert source.status_code == 201, source.text
        mapping = client.post(
            "/api/v1/connectors/tag-mappings", headers=ha,
            json={"data_source_id": source.json()["id"], "connector_tag_id": tag.json()["id"],
                  "sensor_id": a["sensor"], "source_key": "ns=2;s=R101.T"},
        )
        assert mapping.status_code == 201, mapping.text  # writes connector_tag_mappings + source_tag_mappings + audit
        rule = client.post(
            "/api/v1/connectors/validation-rules", headers=ha,
            json={"connector_tag_id": tag.json()["id"], "rule_type": "RANGE", "parameters": {"min": 0}},
        )
        assert rule.status_code == 201, rule.text

        # The connector listing reflects the mapping count written *after* an earlier commit.
        listing = client.get("/api/v1/connectors", headers=ha).json()["items"]
        assert [item["mappings"] for item in listing] == [1]

        dashboard = client.get("/api/v1/dashboard/summary", headers=ha)
        assert dashboard.status_code == 200, dashboard.text
        assert dashboard.json()["connectors"]["count"] == 1
        assert dashboard.json()["measurements"] == {}  # nothing invented without telemetry

        csv_body = "timestamp,TI_RLS\n2026-01-01T00:00:00Z,25.0\n2026-01-01T00:01:00Z,25.5\n"
        mappings = json.dumps({"mappings": [{"source_tag": "TI_RLS", "canonical_name": "reactor.temperature", "unit": "degC"}]})
        preview = client.post(
            "/api/v1/datasets/preview", headers=ha, data={"mappings": mappings},
            files={"file": ("f.csv", io.BytesIO(csv_body.encode()), "text/csv")},
        )
        assert preview.status_code == 200, preview.text
        imported = client.post(
            "/api/v1/datasets/import", headers=ha,
            data={"plant_id": a["plant"], "dataset_name": "RLS import", "mappings": mappings},
            files={"file": ("f.csv", io.BytesIO(csv_body.encode()), "text/csv")},
        )
        assert imported.status_code == 201, imported.text
        assert imported.json()["quality_report"]["status_counts"]["GOOD"] == 2

        # Tenant B sees none of it, and cannot attach to tenant A's objects.
        assert client.get("/api/v1/connectors", headers=hb).json()["items"] == []
        assert client.get("/api/v1/connectors/tags", headers=hb).json()["items"] == []
        assert client.get("/api/v1/datasets", headers=hb).json()["items"] == []
        assert client.get(f"/api/v1/connectors/{source.json()['id']}", headers=hb).status_code == 404
        cross = client.post(
            "/api/v1/connectors/tag-mappings", headers=hb,
            json={"data_source_id": source.json()["id"], "connector_tag_id": tag.json()["id"],
                  "sensor_id": b["sensor"], "source_key": "ns=2;s=x"},
        )
        assert cross.status_code == 404


def test_unscoped_app_role_cannot_read_connector_tables(tenants: dict[str, Any]) -> None:
    from apps.api.processtwin_api.database import SessionLocal
    from sqlalchemy import text

    with SessionLocal() as session:
        for table in (
            "connector_tags", "connector_tag_mappings", "connector_validation_rules",
            "import_jobs", "import_chunks", "object_storage_artifacts",
        ):
            assert session.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table  # noqa: S608
