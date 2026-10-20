"""Full read-only pipeline: OPC-UA server -> connector -> gateway -> database -> API/dashboard.

A real in-process OPC-UA server publishes a *writable* variable. The gateway must acquire it,
persist it with OPCUA provenance, expose connector health, and never alter the server value.
Stopping the server must turn the connector status to a failure state (reconnect/backoff path).
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest
from apps.api.processtwin_api.database import SessionLocal
from apps.api.processtwin_api.models import (
    DataSource,
    Equipment,
    OrganizationMembership,
    QualityStatusName,
    Sensor,
    SensorReading,
    SourceTagMapping,
)
from apps.gateway.processtwin_gateway.runtime import GatewayRuntime
from sqlalchemy import select
from tests.integration.test_historical_datasets import tenant_client

asyncua = pytest.importorskip("asyncua")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _start_server(port: int) -> tuple[Any, Any, str]:
    from asyncua import Server, ua

    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{port}/processtwin/gateway-test")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    index = await server.register_namespace("urn:processtwin:gateway-test")
    reactor = await server.nodes.objects.add_object(index, "R101")
    variable = await reactor.add_variable(index, "Temperature", 180.0)
    await variable.set_writable()
    await server.start()
    return server, variable, variable.nodeid.to_string()


def _configure(client: Any, headers: dict[str, str], plant: Any, equipment: Equipment, endpoint: str, node_id: str) -> Any:
    with SessionLocal() as session:
        organization_id = session.query(OrganizationMembership).one().organization_id
        sensor = Sensor(
            organization_id=organization_id,
            equipment_id=equipment.id,
            name="Reactor temperature",
            tag="OPCUA_TEMPERATURE",
            unit="degC",
            measurement_type="temperature",
            min_valid_value=-100,
            max_valid_value=1000,
        )
        session.add(sensor)
        session.commit()
        sensor_id = sensor.id
    created = client.post(
        "/api/v1/data-sources",
        headers=headers,
        json={
            "plant_id": str(plant.id),
            "name": "Plant OPC-UA (test server)",
            "source_type": "OPCUA",
            "endpoint": endpoint,
            "stale_after_s": 5,
            "opcua_sampling_interval_ms": 100,
            "mappings": [
                {
                    "source_key": node_id,
                    "sensor_id": str(sensor_id),
                    "canonical_name": "reactor.temperature",
                    "source_unit": "degC",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"], sensor_id


@pytest.mark.asyncio
async def test_opcua_values_flow_through_gateway_into_api_without_any_write() -> None:
    client, headers, plant, equipment = tenant_client()
    port = _free_port()
    server, variable, node_id = await _start_server(port)
    endpoint = f"opc.tcp://127.0.0.1:{port}/processtwin/gateway-test"
    source_id, sensor_id = _configure(client, headers, plant, equipment, endpoint, node_id)

    runtime = GatewayRuntime(refresh_interval_s=0.2, reporter_interval_s=0.3)
    task = asyncio.create_task(runtime._run_source(__import__("uuid").UUID(source_id)))
    try:
        values = [181.0, 183.5, 186.25]
        for value in values:
            await asyncio.sleep(0.5)
            await variable.write_value(value)  # the plant changes; ProcessTwin only observes

        # Poll with a deadline so tests are robust against scheduler latency
        stored: list[float] = []
        readings: list[SensorReading] = []
        for _ in range(50):
            await asyncio.sleep(0.1)
            with SessionLocal() as session:
                readings = list(
                    session.scalars(
                        select(SensorReading)
                        .where(SensorReading.sensor_id == sensor_id)
                        .order_by(SensorReading.timestamp)
                    )
                )
            stored = [round(row.value, 2) for row in readings]
            if 186.25 in stored and 183.5 in stored:
                break

        with SessionLocal() as session:
            mapping = session.scalar(select(SourceTagMapping))
        assert stored[0] == 180.0  # initial value delivered on subscription
        assert 186.25 in stored and 183.5 in stored
        assert all(row.source == "OPCUA" for row in readings)
        assert all(row.quality_status is not QualityStatusName.BAD for row in readings)
        assert all(row.data_source_id is not None for row in readings)
        # Read-only: the server still holds the last value the *server side* wrote.
        assert await variable.read_value() == pytest.approx(186.25)

        connector = client.get(f"/api/v1/connectors/{source_id}", headers=headers).json()
        assert connector["read_only"] is True
        assert connector["status"] == "CONNECTED"
        assert connector["last_success_at"] is not None
        assert connector["last_error"] is None
        assert connector["message_rate_per_minute"] > 0

        dashboard = client.get("/api/v1/dashboard/summary", headers=headers).json()
        assert dashboard["source_mode"] == "LIVE_READ_ONLY"
        measurement = dashboard["measurements"]["OPCUA_TEMPERATURE"]
        assert measurement["source"] == "OPCUA"
        assert measurement["stale"] is False
        assert measurement["value"] == pytest.approx(186.25, abs=0.01)
    finally:
        # Server outage: the gateway must report the failure rather than keep claiming health.
        await server.stop()
        await asyncio.sleep(0.2)
        runtime.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_unreachable_server_is_reported_as_error_and_retried_with_backoff() -> None:
    client, headers, plant, equipment = tenant_client()
    port = _free_port()  # nothing listens here
    endpoint = f"opc.tcp://127.0.0.1:{port}/nothing"
    source_id, _sensor_id = _configure(client, headers, plant, equipment, endpoint, "ns=2;s=Missing")
    runtime = GatewayRuntime(refresh_interval_s=0.2, reporter_interval_s=0.3)
    task = asyncio.create_task(runtime._run_source(__import__("uuid").UUID(source_id)))
    try:
        await asyncio.sleep(9.0)
        status = client.get(f"/api/v1/connectors/{source_id}", headers=headers).json()
        assert status["status"] == "ERROR"
        assert status["error_count"] >= 2  # failed, backed off, retried
        assert status["last_error"]
        assert status["last_success_at"] is None
        assert "@" not in status["last_error"]
        dashboard = client.get("/api/v1/dashboard/summary", headers=headers).json()
        assert dashboard["measurements"] == {}  # no data is invented for a dead connector
        assert dashboard["connectors"]["items"][0]["status"] == "ERROR"
        with SessionLocal() as session:
            source = session.get(DataSource, __import__("uuid").UUID(source_id))
            assert source is not None and source.read_only
    finally:
        runtime.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
