"""Live end-to-end workflow smoke test against a running ProcessTwin API.

Exercises the user-facing workflows the launch brief requires:
login -> identity -> dashboard -> plant/equipment -> simulation ->
recommendations -> historical CSV preview -> import -> persisted exploration.

Usage:
    python scripts/smoke_workflows.py [BASE_URL] [EMAIL] [PASSWORD]

It prints one PASS/FAIL line per workflow and exits non-zero if any fail.
This is a demonstration/pilot smoke test; it complements, and does not replace,
the automated pytest suite.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011").rstrip("/")
EMAIL = sys.argv[2] if len(sys.argv) > 2 else "pilot.engineer@processtwin.demo"
PASSWORD = sys.argv[3] if len(sys.argv) > 3 else "SmokeTest-Pilot-1!"

_results: list[tuple[str, bool]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}")


def _send(req: urllib.request.Request) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = response.read().decode()
            return response.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    organization: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if organization:
        req.add_header("X-Organization-ID", organization)
    return _send(req)


def multipart(
    path: str,
    fields: dict[str, str],
    filename: str,
    content: bytes,
    token: str,
    organization: str,
) -> tuple[int, Any]:
    boundary = f"----processtwinSmoke{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/csv\r\n\r\n".encode()
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-Organization-ID", organization)
    return _send(req)


def main() -> int:
    status, _ = request("GET", "/live")
    record("API liveness", status == 200, f"HTTP {status}")
    if status != 200:
        return 1

    status, login = request(
        "POST", "/api/v1/auth/login", body={"email": EMAIL, "password": PASSWORD}
    )
    if status != 200 or not login.get("access_token"):
        record("Authentication", False, f"HTTP {status}")
        return 1
    token = login["access_token"]
    organization = login["organizations"][0]["id"]
    record("Authentication", True, f"role={login['organizations'][0].get('role', '?')}")

    status, me = request("GET", "/api/v1/auth/me", token=token, organization=organization)
    record("Identity (/auth/me)", status == 200 and bool(me.get("email")), f"HTTP {status}")

    status, summary = request(
        "GET", "/api/v1/dashboard/summary", token=token, organization=organization
    )
    mode = summary.get("source_mode") if isinstance(summary, dict) else None
    record("Dashboard summary", status == 200 and mode is not None, f"source_mode={mode}")

    status, plants = request("GET", "/api/v1/plants", token=token, organization=organization)
    items = plants.get("items", []) if isinstance(plants, dict) else []
    plant_id = items[0]["id"] if items else None
    status, plant = (
        request("GET", f"/api/v1/plants/{plant_id}", token=token, organization=organization)
        if plant_id
        else (0, {})
    )
    equipment = plant.get("equipment", []) if isinstance(plant, dict) else []
    equipment_id = equipment[0]["id"] if equipment else None
    record("Plant/equipment hierarchy", bool(plant_id and equipment_id))
    if not plant_id or not equipment_id:
        return 1

    status, simulation = request(
        "POST",
        "/api/v1/simulations",
        token=token,
        organization=organization,
        body={
            "equipment_id": equipment_id,
            "temperature_c": 185.0,
            "pressure_bar": 10.0,
            "flow_m3_h": 72.0,
            "duration_s": 3600,
        },
    )
    trajectory = simulation.get("trajectory", []) if isinstance(simulation, dict) else []
    record(
        "Simulation returns model trajectory",
        status in (200, 201) and len(trajectory) > 1,
        f"points={len(trajectory)}, validity={simulation.get('model_validity') if isinstance(simulation, dict) else None}",
    )

    status, recommendations = request(
        "GET", "/api/v1/recommendations", token=token, organization=organization
    )
    record("Recommendations endpoint", status == 200, f"HTTP {status}")

    csv_body = (
        "timestamp,TI-101,PI-101\n"
        "2026-01-01T00:00:00Z,181.2,9.9\n"
        "2026-01-01T00:05:00Z,181.5,10.1\n"
        "2026-01-01T00:10:00Z,181.1,10.0\n"
    ).encode()
    status, preview = multipart(
        "/api/v1/datasets/preview", {}, "smoke-history.csv", csv_body, token, organization
    )
    suggestions = preview.get("suggested_mappings", []) if isinstance(preview, dict) else []
    mapping_payload = [
        {"source_tag": item["source_tag"], "canonical_name": item["canonical_name"], "unit": item["unit"]}
        for item in suggestions
        if item.get("canonical_name")
    ]
    record(
        "CSV preview detects columns and suggests mappings",
        status == 200 and bool(mapping_payload),
        f"columns={len(preview.get('columns', [])) if isinstance(preview, dict) else 0}, mapped={len(mapping_payload)}",
    )
    if not mapping_payload:
        return 1

    status, imported = multipart(
        "/api/v1/datasets/import",
        {
            "plant_id": plant_id,
            "dataset_name": "Smoke historical import",
            "mappings": json.dumps({"mappings": mapping_payload}),
            "timestamp_column": preview.get("timestamp_column", "timestamp"),
        },
        "smoke-history.csv",
        csv_body,
        token,
        organization,
    )
    version = imported.get("version", {}) if isinstance(imported, dict) else {}
    version_id = version.get("id")
    record(
        "CSV import persists a dataset version",
        status == 201 and bool(version_id),
        f"HTTP {status}, rows={version.get('row_count')}, measurements={version.get('measurement_count')}",
    )
    if not version_id:
        return 1

    status, exploration = request(
        "GET",
        f"/api/v1/datasets/{version_id}/exploration?limit=10",
        token=token,
        organization=organization,
    )
    record("Imported data is explorable", status == 200, f"HTTP {status}")

    failures = [name for name, ok in _results if not ok]
    print(f"\n{len(_results) - len(failures)}/{len(_results)} workflows passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
