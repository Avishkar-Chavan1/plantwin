from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Protocol

from connectors.telemetry import SourceHealth, SourceHealthMonitor, TelemetryMessage

logger = logging.getLogger(__name__)


class OpcUaDataSource(Protocol):
    """Read-only acquisition boundary for customer-configured OPC UA endpoints.

    This protocol is deliberately write-free: no ``write``, ``write_value``,
    ``set``, ``call``, ``publish`` or ``invoke`` operations exist on this boundary.
    Any future protocol implementation must preserve that constraint.
    """

    async def read_value(self, node_id: str) -> tuple[datetime, float, str]: ...

    async def discover_variables(self, root_node_id: str | None = None) -> list[str]: ...

    async def subscribe(
        self,
        node_ids: Mapping[str, tuple[str, str]],
        on_message: Callable[[TelemetryMessage], Coroutine[Any, Any, None]],
    ) -> None: ...


class OpcUaConnectorDisabled:
    """Explicit local-development state—not a claim of PLC connectivity."""

    async def read_value(self, node_id: str) -> tuple[datetime, float, str]:
        raise RuntimeError(f"OPC UA connector is not configured; cannot read {node_id}")

    async def discover_variables(self, root_node_id: str | None = None) -> list[str]:
        raise RuntimeError("OPC UA connector is not configured")

    async def subscribe(
        self,
        node_ids: Mapping[str, tuple[str, str]],
        on_message: Callable[[TelemetryMessage], Coroutine[Any, Any, None]],
    ) -> None:
        raise RuntimeError("OPC UA connector is not configured")


def _status_quality(data_value: Any) -> str:
    """Map an OPC-UA StatusCode to the platform's GOOD/UNCERTAIN/BAD vocabulary.

    A missing status is treated as GOOD only when the server supplies none at all; any
    status that the library can classify as bad/uncertain is propagated so downstream
    quality gates (and therefore recommendations) can reject the sample.
    """
    status = getattr(data_value, "StatusCode_", None) or getattr(data_value, "StatusCode", None)
    if status is None:
        return "GOOD"
    try:
        if status.is_bad():
            return "BAD"
        if hasattr(status, "is_good") and not status.is_good():
            return "UNCERTAIN"
    except Exception:  # classification failure must not drop the sample silently as GOOD
        return "UNCERTAIN"
    return "GOOD"


class _SubscriptionHandler:
    """asyncua data-change callback.

    asyncua invokes ``datachange_notification`` synchronously on the event-loop thread, so the
    handler must never block on work scheduled onto that same loop. It creates a task instead
    and records health when the task completes.
    """

    def __init__(
        self,
        node_map: Mapping[str, tuple[str, str]],
        on_message: Callable[[TelemetryMessage], Coroutine[Any, Any, None]],
        monitor: SourceHealthMonitor,
    ) -> None:
        self.node_map = node_map
        self.on_message = on_message
        self.monitor = monitor
        self._pending: set[asyncio.Task[None]] = set()

    def datachange_notification(self, node: Any, value: Any, data: Any) -> None:
        received = datetime.now(UTC)
        try:
            node_id = node.nodeid.to_string()
            source_key, unit = self.node_map[node_id]
            monitored_value = getattr(getattr(data, "monitored_item", None), "Value", None)
            source_timestamp = getattr(monitored_value, "SourceTimestamp", None)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError("non-numeric OPC-UA value")
            message = TelemetryMessage(
                source_key=source_key,
                value=float(value),
                unit=unit,
                timestamp=source_timestamp or received,
                received_at=received,
                timestamp_source="SOURCE" if source_timestamp else "RECEIVED",
                quality=_status_quality(monitored_value),  # type: ignore[arg-type]
            )
        except Exception as exc:
            self.monitor.record_error(f"sample rejected: {type(exc).__name__}")
            logger.warning("OPC UA sample rejected; reason=%s", type(exc).__name__)
            return
        task = asyncio.get_running_loop().create_task(self._deliver(message))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _deliver(self, message: TelemetryMessage) -> None:
        try:
            await asyncio.wait_for(self.on_message(message), timeout=15)
        except Exception as exc:
            self.monitor.record_message(message, successful=False)
            self.monitor.record_error(f"delivery failed: {type(exc).__name__}")
            logger.warning("OPC UA sample not persisted; reason=%s", type(exc).__name__)
            return
        self.monitor.record_message(message, successful=True)


class OpcUaReadOnlyConnector:
    """Configured read-only OPC-UA adapter; deliberately exposes no node write API.

    The only asyncua operations used are ``connect``/``disconnect``, ``get_node``,
    ``read_data_value``, ``read_node_class``, ``get_children``, ``create_subscription`` and
    ``subscribe_data_change``. Credentials are accepted only as constructor arguments (the
    gateway sources them from environment variables), are never embedded in the endpoint URL,
    and are excluded from ``repr`` and from every log line.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        stale_after_s: float = 30.0,
        timeout_s: float = 10.0,
        client_factory: Callable[[str], Any] | None = None,
        username: str | None = None,
        password: str | None = None,
        security_string: str | None = None,
    ) -> None:
        if not endpoint.startswith(("opc.tcp://", "https://")):
            raise ValueError("OPC-UA endpoint must use opc.tcp:// or https://")
        if "@" in endpoint:
            raise ValueError("Credentials must not be embedded in the OPC-UA endpoint URL")
        if timeout_s <= 0:
            raise ValueError("OPC-UA timeout must be positive")
        if (username is None) != (password is None):
            raise ValueError("OPC-UA username and password must be supplied together")
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.monitor = SourceHealthMonitor(stale_after_s=stale_after_s)
        self._client_factory = client_factory
        self._username = username
        self._password = password
        self._security_string = security_string
        self._client: Any | None = None
        self._subscriptions: list[Any] = []

    def __repr__(self) -> str:  # never include credentials
        return f"OpcUaReadOnlyConnector(endpoint={self.endpoint!r}, connected={self._client is not None})"

    @property
    def health(self) -> SourceHealth:
        return self.monitor.snapshot()

    async def connect(self) -> None:
        try:
            if self._client_factory is None:
                client_factory = import_module("asyncua").Client
                client = client_factory(url=self.endpoint, timeout=self.timeout_s)
            else:
                client = self._client_factory(self.endpoint)
            if self._username is not None and self._password is not None:
                client.set_user(self._username)
                client.set_password(self._password)
            if self._security_string:
                await client.set_security_string(self._security_string)
            await asyncio.wait_for(client.connect(), timeout=self.timeout_s)
            self._client = client
            self.monitor.set_connected(True)
        except ImportError as exc:
            self.monitor.record_error(exc)
            raise RuntimeError("OPC-UA support requires the optional opcua extra") from exc
        except Exception as exc:
            # Only the exception type is recorded: library messages can echo connection strings.
            self.monitor.record_error(f"connect failed: {type(exc).__name__}")
            self.monitor.set_connected(False)
            raise

    async def disconnect(self) -> None:
        for subscription in self._subscriptions:
            try:
                await subscription.delete()
            except Exception as exc:
                self.monitor.record_error(f"unsubscribe failed: {type(exc).__name__}")
        self._subscriptions.clear()
        client = self._client
        self._client = None
        self.monitor.set_connected(False)
        if client is not None:
            try:
                await client.disconnect()
            except Exception as exc:
                self.monitor.record_error(f"disconnect failed: {type(exc).__name__}")

    async def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("OPC-UA connector is disconnected")
        return self._client

    async def check_alive(self) -> None:
        """Raise when the session is unusable so the supervisor can reconnect."""
        client = await self._require_client()
        check = getattr(client, "check_connection", None)
        try:
            if check is not None:
                await asyncio.wait_for(check(), timeout=self.timeout_s)
            self.monitor.set_connected(True)
        except Exception as exc:
            self.monitor.set_connected(False)
            self.monitor.record_error(f"connection lost: {type(exc).__name__}")
            raise

    async def read_value(self, node_id: str) -> tuple[datetime, float, str]:
        client = await self._require_client()
        try:
            data_value = await asyncio.wait_for(
                client.get_node(node_id).read_data_value(), timeout=self.timeout_s
            )
            quality = _status_quality(data_value)
            if quality == "BAD":
                raise RuntimeError("OPC-UA server reported a bad status for the node")
            raw = data_value.Value.Value
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise TypeError("non-numeric OPC-UA value")
            received = datetime.now(UTC)
            timestamp = getattr(data_value, "SourceTimestamp", None) or received
            self.monitor.last_message_at = received
            self.monitor.last_success_at = received
            self.monitor.latency_ms = max(0.0, (received - timestamp).total_seconds() * 1000)
            return timestamp, float(raw), quality
        except Exception as exc:
            self.monitor.record_error(f"read failed: {type(exc).__name__}")
            raise

    async def discover_variables(self, root_node_id: str | None = None) -> list[str]:
        client = await self._require_client()
        root = client.get_node(root_node_id) if root_node_id else client.nodes.objects
        found: list[str] = []
        visited: set[str] = set()

        async def walk(node: Any, depth: int) -> None:
            node_id = node.nodeid.to_string()
            if node_id in visited or depth > 12 or len(found) >= 5_000:
                return
            visited.add(node_id)
            try:
                node_class = await node.read_node_class()
                class_name = getattr(node_class, "name", str(node_class))
                if class_name == "Variable":
                    found.append(node_id)
                    return
                for child in await node.get_children():
                    await walk(child, depth + 1)
            except Exception as exc:
                self.monitor.record_error(f"browse failed: {type(exc).__name__}")
                logger.warning("OPC UA browse node skipped; reason=%s", type(exc).__name__)

        await walk(root, 0)
        return found

    async def subscribe(
        self,
        node_ids: Mapping[str, tuple[str, str]],
        on_message: Callable[[TelemetryMessage], Coroutine[Any, Any, None]],
        *,
        sampling_interval_ms: int = 1000,
    ) -> None:
        client = await self._require_client()
        if sampling_interval_ms < 100:
            raise ValueError("OPC-UA sampling interval must be at least 100 ms")
        handler = _SubscriptionHandler(node_ids, on_message, self.monitor)
        subscription = await client.create_subscription(sampling_interval_ms, handler)
        nodes = [client.get_node(node_id) for node_id in node_ids]
        await subscription.subscribe_data_change(nodes)
        self._subscriptions.append(subscription)
