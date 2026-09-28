from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import pytest
from apps.api.processtwin_api.auth import hash_password
from apps.api.processtwin_api.database import Base, SessionLocal, engine
from apps.api.processtwin_api.main import app
from apps.api.processtwin_api.models import (
    DatasetObservation,
    DatasetVersion,
    Equipment,
    Organization,
    OrganizationMembership,
    Plant,
    RoleName,
    User,
)
from connectors.historical import CsvHistoricalSource, read_historical_source
from fastapi.testclient import TestClient
from packages.data_ingestion import HistoricalMapping, inspect_historical_rows


def tenant_client() -> tuple[TestClient, dict[str, str], Plant, Equipment]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        organization = Organization(name="Historical Import Test Organization")
        user = User(
            email="historical@example.test", password_hash=hash_password("CorrectHorseBattery1")
        )
        session.add_all([organization, user])
        session.flush()
        session.add(
            OrganizationMembership(
                organization_id=organization.id, user_id=user.id, role=RoleName.ENGINEER
            )
        )
        plant = Plant(organization_id=organization.id, name="Import Test Plant")
        session.add(plant)
        session.flush()
        equipment = Equipment(
            organization_id=organization.id,
            plant_id=plant.id,
            tag="R-101",
            name="Reactor",
            equipment_type="CSTR",
        )
        session.add(equipment)
        session.commit()
        plant_id, equipment_id = plant.id, equipment.id
        organization_id = organization.id
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "historical@example.test", "password": "CorrectHorseBattery1"},
    )
    assert login.status_code == 200
    headers = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Organization-ID": str(organization_id),
    }
    with SessionLocal() as session:
        plant = session.get(Plant, plant_id)
        equipment = session.get(Equipment, equipment_id)
        assert plant is not None and equipment is not None
        session.expunge(plant)
        session.expunge(equipment)
    return client, headers, plant, equipment


def mapping_payload(equipment_id: str) -> str:
    return json.dumps(
        {
            "mappings": [
                {
                    "source_tag": "TI_101",
                    "canonical_name": "reactor.temperature",
                    "unit": "degC",
                    "plant_tag": "TI_101",
                    "equipment_id": equipment_id,
                    "minimum_si": 273.15,
                    "maximum_si": 573.15,
                    "expected_sampling_interval_s": 60,
                },
                {
                    "source_tag": "PI_101",
                    "canonical_name": "reactor.pressure",
                    "unit": "bar",
                    "equipment_id": equipment_id,
                },
            ]
        }
    )


def test_csv_historical_source_and_tag_mapping_normalize_preserving_values() -> None:
    rows = CsvHistoricalSource().read(
        b"timestamp,TI_101,PI_101\n2026-01-01T00:00:00Z,25,1.5\n"
    )
    observations = inspect_historical_rows(
        rows,
        [
            HistoricalMapping("TI_101", "reactor.temperature", "degC"),
            HistoricalMapping("PI_101", "reactor.pressure", "bar"),
        ],
    )
    temperature, pressure = observations
    assert temperature.original_value == 25
    assert temperature.original_unit == "degC"
    assert temperature.normalized_value == pytest.approx(298.15)
    assert temperature.normalized_unit == "K"
    assert pressure.normalized_value == pytest.approx(150_000)
    assert pressure.normalized_unit == "Pa"


def test_timestamp_and_quality_checks_keep_each_input_observation() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {"timestamp": start.isoformat(), "TI_101": "200"},
        {"timestamp": (start.replace(minute=1)).isoformat(), "TI_101": "200"},
        {"timestamp": (start.replace(minute=2)).isoformat(), "TI_101": "200"},
        {"timestamp": (start.replace(minute=3)).isoformat(), "TI_101": "200"},
        {"timestamp": (start.replace(minute=4)).isoformat(), "TI_101": "200"},
        {"timestamp": (start.replace(minute=5)).isoformat(), "TI_101": ""},
        {"timestamp": (start.replace(minute=5)).isoformat(), "TI_101": "200"},
        {"timestamp": (start.replace(minute=4)).isoformat(), "TI_101": "201"},
        {"timestamp": "not-a-time", "TI_101": "500"},
    ]
    checked = inspect_historical_rows(
        rows,
        [
            HistoricalMapping(
                "TI_101",
                "reactor.temperature",
                "degC",
                minimum_si=273.15,
                maximum_si=573.15,
                expected_sampling_interval_s=30,
                max_rate_of_change_per_s=0.1,
            )
        ],
    )
    assert len(checked) == 9
    assert checked[5].quality_status.value == "MISSING"
    assert "DUPLICATE_TIMESTAMP" in checked[6].quality_reasons
    assert "OUT_OF_ORDER_TIMESTAMP" in checked[7].quality_reasons
    assert checked[8].quality_status.value == "BAD"
    assert "TIMESTAMP_INVALID" in checked[8].quality_reasons
    assert any("STUCK_SENSOR" in result.quality_reasons for result in checked)


def test_row_unit_mismatch_is_retained_as_bad_with_original_unit() -> None:
    observation = inspect_historical_rows(
        [{"timestamp": "2026-01-01T00:00:00Z", "TI_101": "25", "TI_101_unit": "bar"}],
        [HistoricalMapping("TI_101", "reactor.temperature", "degC")],
    )[0]
    assert observation.original_value == 25
    assert observation.original_unit == "bar"
    assert observation.normalized_value is None
    assert observation.quality_status.value == "BAD"
    assert "UNIT_MISMATCH" in observation.quality_reasons


def test_quality_report_detects_gaps_rate_and_drift() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {"timestamp": start.replace(minute=minute).isoformat(), "TI_101": str(20 + minute)}
        for minute in range(8)
    ]
    rows.append({"timestamp": start.replace(minute=12).isoformat(), "TI_101": "80"})
    observations = inspect_historical_rows(
        rows,
        [
            HistoricalMapping(
                "TI_101",
                "reactor.temperature",
                "degC",
                expected_sampling_interval_s=60,
                max_rate_of_change_per_s=0.01,
                max_drift_per_hour=1.0,
            )
        ],
    )
    final = observations[-1]
    assert "COMMUNICATION_GAP" in final.quality_reasons
    assert "UNREALISTIC_RATE_OF_CHANGE" in final.quality_reasons
    assert "SENSOR_DRIFT" in final.quality_reasons
    assert "SUDDEN_SPIKE" in final.quality_reasons
    assert final.quality_status.value == "SUSPECT"


def test_import_and_exploration_endpoints_are_tenant_scoped() -> None:
    client, headers, plant, equipment = tenant_client()
    response = client.post(
        "/api/v1/datasets/import",
        headers=headers,
        data={
            "plant_id": str(plant.id),
            "dataset_name": "historical trial",
            "timestamp_column": "timestamp",
            "mappings": mapping_payload(str(equipment.id)),
        },
        files={
            "file": (
                "reactor.csv",
                b"timestamp,TI_101,PI_101\n"
                b"2026-01-01T00:00:00Z,25,1.5\n"
                b"2026-01-01T00:01:00Z,26,1.6\n"
                b"2026-01-01T00:02:00Z,,1.7\n",
                "text/csv",
            )
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"]["row_count"] == 3
    assert body["version"]["measurement_count"] == 6
    version_id = body["version"]["id"]
    variables = client.get(f"/api/v1/datasets/{version_id}/variables", headers=headers)
    assert variables.status_code == 200
    assert len(variables.json()["items"]) == 2
    exploration = client.get(f"/api/v1/datasets/{version_id}/exploration", headers=headers)
    assert exploration.status_code == 200
    temperature = next(
        item
        for item in exploration.json()["variables"]
        if item["canonical_name"] == "reactor.temperature"
    )
    assert temperature["normalized_unit"] == "K"
    assert temperature["min"] == pytest.approx(298.15)
    assert temperature["missing_count"] == 1
    assert temperature["missingness_pct"] == pytest.approx(100 / 3)
    assert "reactor.pressure" in exploration.json()["correlation"]
    hierarchy = client.get(
        f"/api/v1/datasets/hierarchy?plant_id={plant.id}", headers=headers
    )
    assert hierarchy.status_code == 200
    assert hierarchy.json()["equipment"][0]["id"] == str(equipment.id)
    assert client.get(f"/api/v1/datasets/{version_id}/exploration").status_code == 401
    headers["X-Organization-ID"] = "00000000-0000-0000-0000-000000000001"
    assert client.get(f"/api/v1/datasets/{version_id}/exploration", headers=headers).status_code == 403
    with SessionLocal() as session:
        assert session.query(DatasetVersion).count() == 1
        persisted = list(session.query(DatasetObservation).all())
        assert len(persisted) == 6
        assert persisted[0].original_value == 25
        assert persisted[0].normalized_value == pytest.approx(298.15)


def test_import_rejects_invalid_mapping_and_plant_scope() -> None:
    client, headers, plant, equipment = tenant_client()
    response = client.post(
        "/api/v1/datasets/import",
        headers=headers,
        data={
            "plant_id": str(plant.id),
            "dataset_name": "invalid map",
            "mappings": "not-json",
        },
        files={"file": ("bad.csv", b"timestamp,TI_101\n2026-01-01T00:00:00Z,25\n")},
    )
    assert response.status_code == 422
    valid_mapping = json.loads(mapping_payload(str(equipment.id)))
    valid_mapping["mappings"][0]["unit"] = "parsec"
    response = client.post(
        "/api/v1/datasets/import",
        headers=headers,
        data={
            "plant_id": str(plant.id),
            "dataset_name": "invalid unit",
            "mappings": json.dumps(valid_mapping),
        },
        files={"file": ("bad.csv", b"timestamp,TI_101,PI_101\n2026-01-01T00:00:00Z,25,1.5\n")},
    )
    assert response.status_code == 422


def test_unsupported_historical_source_extensions_are_rejected() -> None:
    with pytest.raises(ValueError, match="Supported historical"):
        read_historical_source("plant.xlsx", b"not supported")


def test_parquet_historical_source_when_optional_dependency_is_installed() -> None:
    pyarrow = pytest.importorskip("pyarrow", exc_type=ImportError)
    parquet = pytest.importorskip("pyarrow.parquet", exc_type=ImportError)
    sink = io.BytesIO()
    table = pyarrow.Table.from_pylist(
        [{"timestamp": "2026-01-01T00:00:00Z", "TI_101": 25.0}]
    )
    parquet.write_table(table, sink)
    assert read_historical_source("plant.parquet", sink.getvalue()) == [
        {"timestamp": "2026-01-01T00:00:00Z", "TI_101": 25.0}
    ]