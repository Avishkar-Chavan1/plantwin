"""Behavioural tests for the read-only OPC-UA connector.

These use recording fakes (no network) to prove which client operations the connector invokes,
and exercise the subscription callback inside a running event loop, which is where asyncua
invokes it in production.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from connectors.opcua.adapter import OpcUaReadOnlyConnector, _status_quality
from connectors.telemetry import TelemetryMessage

# The complete set of asyncua operations the connector is permitted to use.
READ_ONLY_CLIENT_OPERATIONS = {
    "connect",
    "disconnect",
    "get_node",
    "create_subscription",
    "check_connection",
    "set_user",
    "set_password",
    "set_security_string",
    "nodes",
}
READ_ONLY_NODE_OPERATIONS = {
    "read_data_value",
    "read_node_class",
    "get_children",
    "nodeid",
}


class _Status:
    def __init__(self, bad: bool = False, good: bool = True) -> None:
        self._bad, self._good = bad, good

    def is_bad(self) -> bool:
        return self._bad

    def is_good(self) -> bool:
        return self._good


def _data_value(value: Any, *, bad: bool = False, uncertain: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        Value=SimpleNamespace(Value=value),
        SourceTimestamp=datetime(2026, 1, 1, tzinfo=UTC),
        StatusCode_=_Status(bad=bad, good=not (bad or uncertain)),
    )


class _Node:
    def __init__(self, calls: list[str], node_id: str, data_value: Any = None, children=()) -> None:
        self._calls = calls
        self.nodeid = SimpleNamespace(to_string=lambda: node_id)
        self._data_value = data_value
        self._children = list(children)

    async def read_data_value(self) -> Any:
        self._calls.append("node.read_data_value")
        return self._data_value

    async def read_node_class(self) -> Any:
        self._calls.append("node.read_node_class")
        return SimpleNamespace(name="Variable" if not self._children else "Object")

    async def get_children(self) -> list[Any]:
        self._calls.append("node.get_children")
        return self._children


class _Subscription:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def subscribe_data_change(self, nodes: list[Any]) -> None:
        self._calls.append("subscription.subscribe_data_change")

    async def delete(self) -> None:
        self._calls.append("subscription.delete")


class _Client:
    def __init__(self, calls: list[str], *, fail_connect: bool = False) -> None:
        self.calls = calls
        self.handler: Any = None
        self.fail_connect = fail_connect
        self.user: str | None = None
        self.password: str | None = None
        self.alive = True
        leaf = _Node(calls, "ns=2;s=R101.Temperature", _data_value(180.5))
        self.nodes = SimpleNamespace(objects=_Node(calls, "i=85", children=[leaf]))
        self._leaf = leaf

    def set_user(self, user: str) -> None:
        self.calls.append("set_user")
        self.user = user

    def set_password(self, password: str) -> None:
        self.calls.append("set_password")
        self.password = password

    async def connect(self) -> None:
        self.calls.append("connect")
        if self.fail_connect:
            raise ConnectionRefusedError(f"cannot reach server as {self.user}:{self.password}")

    async def disconnect(self) -> None:
        self.calls.append("disconnect")

    async def check_connection(self) -> None:
        self.calls.append("check_connection")
        if not self.alive:
            raise ConnectionError("session closed")

    def get_node(self, node_id: str) -> _Node:
        self.calls.append("get_node")
        return _Node(
            self.calls,
            node_id,
            self._leaf._data_value,
        )

    async def create_subscription(self, interval_ms: int, handler: Any) -> _Subscription:
        self.calls.append("create_subscription")
        self.handler = handler
        return _Subscription(self.calls)


def _connector(client: _Client, **kwargs: Any) -> OpcUaReadOnlyConnector:
    return OpcUaReadOnlyConnector(
        "opc.tcp://opcua.example:4840", client_factory=lambda _url: client, **kwargs
    )


@pytest.mark.asyncio
async def test_full_lifecycle_uses_only_read_operations() -> None:
    calls: list[str] = []
    client = _Client(calls)
    connector = _connector(client)
    await connector.connect()
    timestamp, value, quality = await connector.read_value("ns=2;s=R101.Temperature")
    discovered = await connector.discover_variables()
    await connector.subscribe(
        {"ns=2;s=R101.Temperature": ("R101.Temperature", "degC")}, _noop, sampling_interval_ms=100
    )
    await connector.check_alive()
    await connector.disconnect()

    assert (value, quality) == (180.5, "GOOD")
    assert timestamp.tzinfo is not None
    assert discovered == ["ns=2;s=R101.Temperature"]
    client_ops = {call.split(".")[0] for call in calls if not call.startswith(("node.", "subscription."))}
    assert client_ops <= READ_ONLY_CLIENT_OPERATIONS, sorted(client_ops - READ_ONLY_CLIENT_OPERATIONS)
    node_ops = {call.removeprefix("node.") for call in calls if call.startswith("node.")}
    assert node_ops <= READ_ONLY_NODE_OPERATIONS
    sub_ops = {call.removeprefix("subscription.") for call in calls if call.startswith("subscription.")}
    assert sub_ops <= {"subscribe_data_change", "delete"}


async def _noop(message: TelemetryMessage) -> None:
    return None


@pytest.mark.asyncio
async def test_bad_status_and_non_numeric_values_are_rejected() -> None:
    calls: list[str] = []
    client = _Client(calls)
    connector = _connector(client)
    await connector.connect()
    client._leaf._data_value = _data_value(1.0, bad=True)
    with pytest.raises(RuntimeError, match="bad status"):
        await connector.read_value("ns=2;s=x")
    client._leaf._data_value = _data_value("not-a-number")
    with pytest.raises(TypeError):
        await connector.read_value("ns=2;s=x")
    client._leaf._data_value = _data_value(True)
    with pytest.raises(TypeError):
        await connector.read_value("ns=2;s=x")
    assert connector.health.error_count == 3


def test_status_quality_mapping() -> None:
    assert _status_quality(_data_value(1.0)) == "GOOD"
    assert _status_quality(_data_value(1.0, bad=True)) == "BAD"
    assert _status_quality(_data_value(1.0, uncertain=True)) == "UNCERTAIN"
    assert _status_quality(SimpleNamespace()) == "GOOD"


@pytest.mark.asyncio
async def test_subscription_callback_runs_on_the_event_loop_without_deadlock() -> None:
    """asyncua calls the handler on the loop thread; blocking on that loop would deadlock."""
    calls: list[str] = []
    client = _Client(calls)
    connector = _connector(client)
    await connector.connect()
    received: list[TelemetryMessage] = []

    async def on_message(message: TelemetryMessage) -> None:
        received.append(message)

    await connector.subscribe(
        {"ns=2;s=R101.Temperature": ("R101.Temperature", "degC")}, on_message
    )
    node = SimpleNamespace(nodeid=SimpleNamespace(to_string=lambda: "ns=2;s=R101.Temperature"))
    good = SimpleNamespace(monitored_item=SimpleNamespace(Value=_data_value(180.5)))
    bad = SimpleNamespace(monitored_item=SimpleNamespace(Value=_data_value(0.0, bad=True)))
    client.handler.datachange_notification(node, 180.5, good)  # synchronous, on the loop
    client.handler.datachange_notification(node, 0.0, bad)
    client.handler.datachange_notification(node, "garbage", good)
    unknown = SimpleNamespace(nodeid=SimpleNamespace(to_string=lambda: "ns=2;s=Unmapped"))
    client.handler.datachange_notification(unknown, 1.0, good)
    await asyncio.sleep(0.05)

    assert [(m.source_key, m.value, m.quality) for m in received] == [
        ("R101.Temperature", 180.5, "GOOD"),
        ("R101.Temperature", 0.0, "BAD"),
    ]
    assert all(m.timestamp_source == "SOURCE" for m in received)
    health = connector.health
    assert health.last_success_at is not None
    assert health.error_count == 2  # the non-numeric and the unmapped sample


@pytest.mark.asyncio
async def test_delivery_failure_is_counted_not_swallowed() -> None:
    client = _Client([])
    connector = _connector(client)
    await connector.connect()

    async def failing(message: TelemetryMessage) -> None:
        raise RuntimeError("database unavailable")

    await connector.subscribe({"ns=2;s=a": ("a", "degC")}, failing)
    node = SimpleNamespace(nodeid=SimpleNamespace(to_string=lambda: "ns=2;s=a"))
    data = SimpleNamespace(monitored_item=SimpleNamespace(Value=_data_value(1.0)))
    client.handler.datachange_notification(node, 1.0, data)
    await asyncio.sleep(0.05)
    assert connector.health.error_count == 1
    assert connector.health.last_error is not None
    assert "database unavailable" not in connector.health.last_error


@pytest.mark.asyncio
async def test_credentials_are_never_exposed_in_repr_or_errors() -> None:
    client = _Client([], fail_connect=True)
    connector = _connector(client, username="svc-reader", password="s3cret-pw!")
    assert "s3cret-pw!" not in repr(connector)
    assert "svc-reader" not in repr(connector)
    with pytest.raises(ConnectionRefusedError):
        await connector.connect()
    assert client.user == "svc-reader"
    health = connector.health
    assert health.status == "ERROR"
    assert "s3cret-pw!" not in (health.last_error or "")
    assert "svc-reader" not in (health.last_error or "")


def test_username_without_password_is_rejected() -> None:
    with pytest.raises(ValueError, match="together"):
        OpcUaReadOnlyConnector("opc.tcp://host:4840", username="only-user")


@pytest.mark.asyncio
async def test_lost_session_is_reported_so_the_gateway_reconnects() -> None:
    client = _Client([])
    connector = _connector(client)
    await connector.connect()
    await connector.check_alive()
    client.alive = False
    with pytest.raises(ConnectionError):
        await connector.check_alive()
    assert connector.health.status in {"ERROR", "DISCONNECTED"}


@pytest.mark.asyncio
async def test_operations_require_a_connection() -> None:
    connector = _connector(_Client([]))
    with pytest.raises(RuntimeError, match="disconnected"):
        await connector.read_value("ns=2;s=x")
    with pytest.raises(ValueError, match="at least 100 ms"):
        await connector.connect()
        await connector.subscribe({}, _noop, sampling_interval_ms=50)
