"""Guided CSV upload: detect columns -> map -> validate -> preview -> import -> explore."""

from __future__ import annotations

import io
import json

import pytest
from apps.api.processtwin_api.database import SessionLocal
from apps.api.processtwin_api.dataset_preview import (
    TimezoneError,
    analyze_columns,
    guess_timestamp_column,
    parse_assumed_timezone,
    suggest_mappings,
)
from apps.api.processtwin_api.models import DatasetObservation, QualityStatusName
from sqlalchemy import func, select
from tests.integration.test_historical_datasets import tenant_client

CSV_UTC = (
    "timestamp,TI_101,PI_101,FI_101\n"
    "2026-01-01T00:00:00Z,180.1,10.0,72.0\n"
    "2026-01-01T00:01:00Z,180.4,10.1,72.5\n"
    "2026-01-01T00:02:00Z,180.2,10.0,71.9\n"
    "2026-01-01T00:03:00Z,180.9,10.2,72.2\n"
)
MAPPINGS = {
    "mappings": [
        {"source_tag": "TI_101", "canonical_name": "reactor.temperature", "unit": "degC"},
        {"source_tag": "PI_101", "canonical_name": "reactor.pressure", "unit": "bar"},
        {"source_tag": "FI_101", "canonical_name": "reactor.feed_flow", "unit": "m3/h"},
    ]
}


def _upload(client, headers, path: str, content: bytes | str, **data: str):  # type: ignore[no-untyped-def]
    raw = content.encode() if isinstance(content, str) else content
    return client.post(
        path,
        headers=headers,
        files={"file": ("historian.csv", io.BytesIO(raw), "text/csv")},
        data=data,
    )


def test_preview_detects_columns_and_suggests_mappings_without_storing_anything() -> None:
    client, headers, plant, _equipment = tenant_client()
    response = _upload(client, headers, "/api/v1/datasets/preview", CSV_UTC)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["row_count"] == 4
    assert body["timestamp_column"] == "timestamp"
    kinds = {column["name"]: column["kind"] for column in body["columns"]}
    assert kinds == {
        "timestamp": "timestamp",
        "TI_101": "numeric",
        "PI_101": "numeric",
        "FI_101": "numeric",
    }
    suggestions = {item["source_tag"]: item for item in body["suggested_mappings"]}
    assert suggestions["TI_101"]["canonical_name"] == "reactor.temperature"
    assert suggestions["TI_101"]["unit"] == "degC"
    assert suggestions["PI_101"]["unit"] == "bar"
    assert suggestions["FI_101"]["canonical_name"] == "reactor.feed_flow"
    assert body["timestamp"]["parsed_count"] == 4
    assert body["timestamp"]["issues"] == []
    assert "degC" in body["supported_units"]
    assert body["validation"] is None
    assert len(body["sample_rows"]) == 4
    with SessionLocal() as session:  # a preview never writes
        assert session.scalar(select(func.count()).select_from(DatasetObservation)) == 0


def test_preview_explains_mapping_to_columns_that_are_not_in_the_file() -> None:
    """Regression: the old UI pre-filled TI/PI/FI mappings regardless of the uploaded file."""
    client, headers, _plant, _equipment = tenant_client()
    csv_text = "time,temp_C,press_bar\n2026-01-01T00:00:00Z,25,1\n2026-01-01T00:01:00Z,26,1\n"
    response = _upload(
        client,
        headers,
        "/api/v1/datasets/preview",
        csv_text,
        mappings=json.dumps(MAPPINGS),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["timestamp_column"] == "time"
    assert body["validation"] is None
    message = " ".join(body["blocking_errors"])
    assert "FI_101" in message and "PI_101" in message and "TI_101" in message
    assert "temp_C" in message and "press_bar" in message  # tells the user what *is* there


def test_preview_validation_reports_the_exact_import_outcome() -> None:
    client, headers, _plant, _equipment = tenant_client()
    response = _upload(
        client,
        headers,
        "/api/v1/datasets/preview",
        CSV_UTC,
        mappings=json.dumps(MAPPINGS),
    )
    body = response.json()
    assert body["blocking_errors"] == []
    validation = body["validation"]
    assert validation["measurements"] == 12
    assert validation["status_counts"]["GOOD"] == 12
    assert validation["usable_fraction"] == 1.0
    assert validation["warning"] is None


def test_naive_timestamps_are_rejected_until_the_user_chooses_a_time_zone() -> None:
    client, headers, _plant, _equipment = tenant_client()
    naive = CSV_UTC.replace("Z", "").replace("T0", " 0")
    unresolved = _upload(
        client, headers, "/api/v1/datasets/preview", naive, mappings=json.dumps(MAPPINGS)
    ).json()
    assert unresolved["timestamp"]["no_timezone_count"] == 4
    assert "time zone" in " ".join(unresolved["timestamp"]["issues"])
    assert unresolved["validation"]["status_counts"]["BAD"] == 12
    assert "More than half" in unresolved["validation"]["warning"]

    resolved = _upload(
        client,
        headers,
        "/api/v1/datasets/preview",
        naive,
        mappings=json.dumps(MAPPINGS),
        assume_timezone="Europe/Berlin",
    ).json()
    assert resolved["timestamp"]["no_timezone_count"] == 0
    assert resolved["timestamp"]["first"].startswith("2025-12-31T23:00:00")  # CET -> UTC
    assert resolved["validation"]["status_counts"]["GOOD"] == 12

    bad_zone = _upload(
        client,
        headers,
        "/api/v1/datasets/preview",
        naive,
        mappings=json.dumps(MAPPINGS),
        assume_timezone="Mars/Olympus",
    )
    assert bad_zone.status_code == 422
    assert bad_zone.json()["error"]["code"] == "INVALID_TIMEZONE"


def test_corrupt_values_are_flagged_never_silently_accepted() -> None:
    client, headers, _plant, _equipment = tenant_client()
    csv_text = (
        "timestamp,TI_101,PI_101,FI_101\n"
        "2026-01-01T00:00:00Z,180.1,10.0,72.0\n"
        "2026-01-01T00:00:00Z,180.2,10.0,72.0\n"  # duplicate timestamp
        "not-a-time,180.3,10.0,72.0\n"  # invalid timestamp
        "2026-01-01T00:03:00Z,abc,10.0,72.0\n"  # non-numeric
        "2026-01-01T00:04:00Z,,10.0,72.0\n"  # missing
        "2026-01-01T00:05:00Z,nan,10.0,72.0\n"  # non-finite
    )
    mappings = json.dumps(
        {
            "mappings": [
                {"source_tag": "TI_101", "canonical_name": "reactor.temperature", "unit": "degC"},
            ]
        }
    )
    body = _upload(
        client, headers, "/api/v1/datasets/preview", csv_text, mappings=mappings
    ).json()
    reasons = body["validation"]["reason_counts"]
    assert reasons["DUPLICATE_TIMESTAMP"] >= 1
    assert reasons["TIMESTAMP_INVALID"] == 1
    assert reasons["NON_NUMERIC_VALUE"] == 1
    assert reasons["MISSING_VALUE"] == 1
    assert reasons["NON_FINITE_VALUE"] == 1
    counts = body["validation"]["status_counts"]
    assert counts["GOOD"] == 1  # only the first row is clean
    assert body["timestamp"]["duplicate_count"] == 1
    assert body["timestamp"]["invalid_count"] == 1


def test_wrong_units_are_marked_unit_mismatch() -> None:
    client, headers, _plant, _equipment = tenant_client()
    csv_text = "timestamp,TI_101,TI_101_unit\n2026-01-01T00:00:00Z,350,bar\n2026-01-01T00:01:00Z,25,degC\n"
    mappings = json.dumps(
        {"mappings": [{"source_tag": "TI_101", "canonical_name": "reactor.temperature", "unit": "degC"}]}
    )
    body = _upload(client, headers, "/api/v1/datasets/preview", csv_text, mappings=mappings).json()
    assert body["validation"]["reason_counts"]["UNIT_MISMATCH"] == 1
    # The per-row unit column is consumed by the importer and not offered as a measurement.
    assert [item["source_tag"] for item in body["suggested_mappings"]] == ["TI_101"]


def test_unsupported_unit_in_mapping_is_a_blocking_error() -> None:
    client, headers, _plant, _equipment = tenant_client()
    mappings = json.dumps(
        {"mappings": [{"source_tag": "TI_101", "canonical_name": "t", "unit": "furlongs"}]}
    )
    body = _upload(client, headers, "/api/v1/datasets/preview", CSV_UTC, mappings=mappings).json()
    assert "Unsupported unit" in " ".join(body["blocking_errors"])


@pytest.mark.parametrize(
    ("content", "status", "code"),
    [
        (b"", 422, "EMPTY_FILE"),
        (b"timestamp,TI_101\n", 422, "MALFORMED_FILE"),  # header only
        (b"\xff\xfe\x00binary\x80", 422, "MALFORMED_FILE"),
    ],
)
def test_empty_malformed_and_binary_files_get_actionable_errors(
    content: bytes, status: int, code: str
) -> None:
    client, headers, _plant, _equipment = tenant_client()
    response = _upload(client, headers, "/api/v1/datasets/preview", content)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"]


def test_wrong_extension_and_oversized_files_are_rejected() -> None:
    client, headers, _plant, _equipment = tenant_client()
    wrong = client.post(
        "/api/v1/datasets/preview",
        headers=headers,
        files={"file": ("data.xlsx", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "INVALID_FILE_TYPE"
    row = "2026-01-01T00:00:00Z," + "1.5," * 40 + "1.5\n"
    big = ("timestamp," + ",".join(f"c{i}" for i in range(41)) + "\n" + row * 40_000).encode()
    assert len(big) > 5_000_000
    response = _upload(client, headers, "/api/v1/datasets/preview", big)
    assert response.status_code == 413


def test_end_to_end_import_after_preview_stores_exactly_what_was_validated() -> None:
    client, headers, plant, equipment = tenant_client()
    naive = CSV_UTC.replace("Z", "")
    response = _upload(
        client,
        headers,
        "/api/v1/datasets/import",
        naive,
        plant_id=str(plant.id),
        dataset_name="Reactor trial",
        timestamp_column="timestamp",
        mappings=json.dumps(MAPPINGS),
        assume_timezone="UTC",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"]["row_count"] == 4
    assert body["version"]["measurement_count"] == 12
    assert body["quality_report"]["status_counts"]["GOOD"] == 12
    version_id = body["version"]["id"]

    with SessionLocal() as session:
        stored = list(session.scalars(select(DatasetObservation)))
        assert len(stored) == 12
        assert all(row.timestamp is not None for row in stored)
        assert all(row.quality_status is QualityStatusName.GOOD for row in stored)

    exploration = client.get(
        f"/api/v1/datasets/{version_id}/exploration?plant_id={plant.id}", headers=headers
    )
    assert exploration.status_code == 200, exploration.text
    names = {variable["canonical_name"] for variable in exploration.json()["variables"]}
    assert names == {"reactor.temperature", "reactor.pressure", "reactor.feed_flow"}
    assert exploration.json()["trends"]["reactor.temperature"]

    # Importing the naive file *without* a time zone must not slip through as good data.
    rejected = _upload(
        client,
        headers,
        "/api/v1/datasets/import",
        naive,
        plant_id=str(plant.id),
        dataset_name="No zone",
        mappings=json.dumps(MAPPINGS),
    )
    assert rejected.status_code == 201
    assert rejected.json()["quality_report"]["status_counts"]["GOOD"] == 0
    assert rejected.json()["quality_report"]["reason_counts"]["TIMESTAMP_TIMEZONE_MISSING"] == 12


def test_import_reports_uploaded_columns_that_were_never_mapped() -> None:
    """An uploaded column with no mapping is dropped, and the response must say so."""
    client, headers, plant, _equipment = tenant_client()
    csv_text = (
        "timestamp,TI_101,PI_101,ORPHAN_99\n"
        "2026-01-01T00:00:00Z,180.1,10.0,5.0\n"
        "2026-01-01T00:01:00Z,180.4,10.1,5.5\n"
    )
    mappings = json.dumps(
        {
            "mappings": [
                {"source_tag": "TI_101", "canonical_name": "reactor.temperature", "unit": "degC"},
                {"source_tag": "PI_101", "canonical_name": "reactor.pressure", "unit": "bar"},
            ]
        }
    )
    response = _upload(
        client,
        headers,
        "/api/v1/datasets/import",
        csv_text,
        plant_id=str(plant.id),
        dataset_name="Orphan column",
        timestamp_column="timestamp",
        mappings=mappings,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["unmapped_source_tags"] == ["ORPHAN_99"]
    assert any("ORPHAN_99" in warning for warning in body["warnings"])
    # Only the two mapped tags are stored; the unmapped column never becomes data.
    assert body["version"]["measurement_count"] == 4


def test_import_with_unmapped_columns_returns_a_message_naming_the_detected_columns() -> None:
    client, headers, plant, _equipment = tenant_client()
    response = _upload(
        client,
        headers,
        "/api/v1/datasets/import",
        "time,temp_C\n2026-01-01T00:00:00Z,25\n",
        plant_id=str(plant.id),
        dataset_name="Mismatch",
        mappings=json.dumps(MAPPINGS),
    )
    assert response.status_code == 422
    message = response.json()["error"]["message"]
    assert "FI_101" in message and "temp_C" in message


# ------------------------------------------------------------------ pure helper tests


def test_column_analysis_and_timestamp_guessing() -> None:
    rows = [
        {"when": "2026-01-01T00:00:00Z", "a": "1", "b": "x", "c": ""},
        {"when": "2026-01-01T00:01:00Z", "a": "2.5", "b": "y", "c": ""},
    ]
    columns = analyze_columns(rows)
    assert {column["name"]: column["kind"] for column in columns} == {
        "when": "timestamp",
        "a": "numeric",
        "b": "text",
        "c": "empty",
    }
    assert guess_timestamp_column(columns, None) == "when"
    assert guess_timestamp_column(columns, "a") == "a"
    assert guess_timestamp_column(columns, "missing-column") == "when"
    assert [item["source_tag"] for item in suggest_mappings(columns, "when")] == ["a"]


def test_timezone_parsing() -> None:
    assert parse_assumed_timezone(None) is None
    assert parse_assumed_timezone("  ") is None
    assert parse_assumed_timezone("utc") is not None
    with pytest.raises(TimezoneError):
        parse_assumed_timezone("Not/AZone")
