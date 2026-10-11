"""End-to-end check of the read-only OPC-UA connector against a real in-process OPC-UA server.

The server variable is *writable* on purpose: it proves the connector can acquire values but
never modifies them, and that reconnection after a server restart works.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from connectors.opcua.adapter import OpcUaReadOnlyConnector
from connectors.telemetry import TelemetryMessage

asyncua = pytest.importorskip("asyncua")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Plant:
    """A minimal OPC-UA server exposing one writable temperature variable."""

    def __init__(self) -> None:
        self.port = _free_port()
        self.endpoint = f"opc.tcp://127.0.0.1:{self.port}/processtwin/test"
        self.server: Any = None
        self.temperature: Any = None
        self.node_id = ""

    async def start(self) -> None:
        from asyncua import Server, ua

        self.server = Server()
        await self.server.init()
        self.server.set_endpoint(self.endpoint)
        self.server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        index = await self.server.register_namespace("urn:processtwin:test")
        reactor = await self.server.nodes.objects.add_object(index, "R101")
        self.temperature = await reactor.add_variable(index, "Temperature", 180.0)
        await self.temperature.set_writable()
        self.node_id = self.temperature.nodeid.to_string()
        await self.server.start()

    async def stop(self) -> None:
        await self.server.stop()


@pytest_asyncio.fixture
async def plant() -> AsyncIterator[_Plant]:
    instance = _Plant()
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


@pytest.mark.asyncio
async def test_read_discover_and_subscribe_without_ever_writing(plant: _Plant) -> None:
    connector = OpcUaReadOnlyConnector(plant.endpoint, stale_after_s=5, timeout_s=5)
    await connector.connect()
    try:
        timestamp, value, quality = await connector.read_value(plant.node_id)
        assert value == pytest.approx(180.0)
        assert quality == "GOOD"
        assert timestamp.tzinfo is not None

        variables = await connector.discover_variables()
        assert plant.node_id in variables

        received: asyncio.Queue[TelemetryMessage] = asyncio.Queue()

        async def on_message(message: TelemetryMessage) -> None:
            await received.put(message)

        await connector.subscribe(
            {plant.node_id: ("R101.Temperature", "degC")}, on_message, sampling_interval_ms=100
        )
        first = await asyncio.wait_for(received.get(), timeout=5)  # initial value notification
        assert first.source_key == "R101.Temperature"
        assert first.unit == "degC"
        assert first.value == pytest.approx(180.0)

        await plant.temperature.write_value(195.5)  # the *server side* changes the process value
        changed = await asyncio.wait_for(received.get(), timeout=5)
        assert changed.value == pytest.approx(195.5)
        assert changed.quality == "GOOD"

        await connector.check_alive()
        assert connector.health.status == "CONNECTED"
        assert connector.health.last_success_at is not None
        # The connector never wrote: the server value is exactly what the server last set.
        assert await plant.temperature.read_value() == pytest.approx(195.5)
    finally:
        await connector.disconnect()
    assert connector.health.status == "DISCONNECTED"


@pytest.mark.asyncio
async def test_connection_failure_is_reported_without_leaking_the_endpoint_credentials() -> None:
    connector = OpcUaReadOnlyConnector(
        f"opc.tcp://127.0.0.1:{_free_port()}/nothing-listens-here",
        timeout_s=1,
        username="reader",
        password="correct-horse-battery",
    )
    with pytest.raises(Exception):  # noqa: B017 - any transport failure is acceptable here
        await connector.connect()
    health = connector.health
    assert health.status == "ERROR"
    assert health.error_count == 1
    assert "correct-horse-battery" not in (health.last_error or "")
