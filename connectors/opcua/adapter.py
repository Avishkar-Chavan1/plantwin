from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping
from concurrent.futures import Future
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


class _SubscriptionHandler:
    def __init__(
        self,
        node_map: Mapping[str, tuple[str, str]],
        on_message: Callable[[TelemetryMessage], Coroutine[Any, Any, None]],
        monitor: SourceHealthMonitor,
    ) -> None:
        self.node_map = node_map
        self.on_message = on_message
        self.monitor = monitor

    def datachange_notification(self, node: Any, value: Any, data: Any) -> None:
        received = datetime.now(UTC)
        try:
            node_id = node.nodeid.to_string()
            source_key, unit = self.node_map[node_id]
            source_timestamp = getattr(getattr(data, "monitored_item", None), "Value", None)
            source_timestamp = getattr(source_timestamp, "SourceTimestamp", None)
            timestamp = source_timestamp or received
            message = TelemetryMessage(
                source_key=source_key,
                value=float(value),
                unit=unit,
                timestamp=timestamp,
                received_at=received,
                timestamp_source="SOURCE" if source_timestamp else "RECEIVED",
            )
            future: Future[None] = asyncio.run_coroutine_threadsafe(
                self.on_message(message), self.loop
            )
            future.result(timeout=15)
            self.monitor.record_message(message, successful=True)
        except Exception as exc:
            if "message" in locals():
                self.monitor.record_message(message, successful=False)
            self.monitor.record_error(exc)
            logger.warning("OPC UA sample rejected; reason=%s", type(exc).__name__)

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop


class OpcUaReadOnlyConnector:
    """Configured read-only OPC-UA adapter; deliberately exposes no node write API."""

    def __init__(
        self,
        endpoint: str,
        *,
        stale_after_s: float = 30.0,
        timeout_s: float = 10.0,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        if not endpoint.startswith(("opc.tcp://", "https://")):
            raise ValueError("OPC-UA endpoint must use opc.tcp:// or https://")
        if timeout_s <= 0:
            raise ValueError("OPC-UA timeout must be positive")
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self.monitor = SourceHealthMonitor(stale_after_s=stale_after_s)
        self._client_factory = client_factory
        self._client: Any | None = None
        self._subscriptions: list[Any] = []

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
            await asyncio.wait_for(client.connect(), timeout=self.timeout_s)
            self._client = client
            self.monitor.set_connected(True)
        except ImportError as exc:
            self.monitor.record_error(exc)
            raise RuntimeError("OPC-UA support requires the optional opcua extra") from exc
        except Exception as exc:
            self.monitor.record_error(exc)
            self.monitor.set_connected(False)
            raise

    async def disconnect(self) -> None:
        for subscription in self._subscriptions:
            try:
                await subscription.delete()
            except Exception as exc:
                self.monitor.record_error(exc)
        self._subscriptions.clear()
        client = self._client
        self._client = None
        if client is not None:
            await client.disconnect()
        self.monitor.set_connected(False)

    async def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("OPC-UA connector is disconnected")
        return self._client

    async def read_value(self, node_id: str) -> tuple[datetime, float, str]:
        client = await self._require_client()
        try:
            data_value = await asyncio.wait_for(
                client.get_node(node_id).read_data_value(), timeout=self.timeout_s
            )
            source_timestamp = getattr(data_value, "SourceTimestamp", None)
            timestamp = source_timestamp or datetime.now(UTC)
            value = float(data_value.Value.Value)
            self.monitor.last_message_at = datetime.now(UTC)
            self.monitor.last_success_at = self.monitor.last_message_at
            self.monitor.latency_ms = 0.0
            return timestamp, value, ""
        except Exception as exc:
            self.monitor.record_error(exc)
            raise

    async def discover_variables(self, root_node_id: str | None = None) -> list[str]:
        client = await self._require_client()
        root = client.get_node(root_node_id) if root_node_id else client.nodes.objects
        found: list[str] = []
        visited: set[str] = set()

        async def walk(node: Any) -> None:
            node_id = node.nodeid.to_string()
            if node_id in visited:
                return
            visited.add(node_id)
            try:
                node_class = await node.read_node_class()
                class_name = getattr(node_class, "name", str(node_class))
                if class_name == "Variable":
                    found.append(node_id)
                    return
                for child in await node.get_children():
                    await walk(child)
            except Exception as exc:
                self.monitor.record_error(exc)
                logger.warning("OPC UA browse node skipped; reason=%s", type(exc).__name__)

        await walk(root)
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
        loop = asyncio.get_running_loop()
        handler = _SubscriptionHandler(node_ids, on_message, self.monitor)
        handler.set_loop(loop)
        subscription = await client.create_subscription(sampling_interval_ms, handler)
        nodes = [client.get_node(node_id) for node_id in node_ids]
        await subscription.subscribe_data_change(nodes)
        self._subscriptions.append(subscription)
