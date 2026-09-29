from __future__ import annotations

import asyncio
import logging
from typing import Any, TypedDict
from urllib.parse import urlsplit
from uuid import UUID

from connectors.mqtt.adapter import MqttReadOnlySubscriber
from connectors.opcua.adapter import OpcUaReadOnlyConnector
from connectors.telemetry import SourceHealth, TelemetryMessage
from sqlalchemy import select

from apps.api.processtwin_api.database import SessionLocal
from apps.api.processtwin_api.models import DataSource, SourceTagMapping
from apps.api.processtwin_api.realtime import LiveIngestionGateway

logger = logging.getLogger("processtwin.gateway")


class GatewaySource(TypedDict):
    id: UUID
    organization_id: UUID
    source_type: str
    endpoint: str
    configuration: dict[str, Any]


class GatewayRuntime:
    """Standalone configured connector runtime. All connector interfaces are read-only."""

    def __init__(
        self, *, refresh_interval_s: float = 5.0, reporter_interval_s: float = 5.0
    ) -> None:
        self.refresh_interval_s = refresh_interval_s
        self.reporter_interval_s = reporter_interval_s
        self._stop = asyncio.Event()
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._gateway = LiveIngestionGateway()

    async def run(self) -> None:
        logger.info("Starting ProcessTwin read-only ingestion gateway")
        try:
            while not self._stop.is_set():
                with SessionLocal() as session:
                    source_ids = set(
                        session.scalars(select(DataSource.id).where(DataSource.read_only.is_(True)))
                    )
                for source_id in source_ids:
                    task = self._tasks.get(source_id)
                    if task is None or task.done():
                        self._tasks[source_id] = asyncio.create_task(self._run_source(source_id))
                for source_id in list(self._tasks):
                    if source_id not in source_ids:
                        self._tasks[source_id].cancel()
                        del self._tasks[source_id]
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.refresh_interval_s)
                except TimeoutError:
                    pass
        finally:
            for task in self._tasks.values():
                task.cancel()
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
            self._tasks.clear()

    def stop(self) -> None:
        self._stop.set()

    def _load_source(
        self, source_id: UUID
    ) -> tuple[GatewaySource | None, dict[str, tuple[str, str]]]:
        with SessionLocal() as session:
            source = session.get(DataSource, source_id)
            if source is None or not source.read_only or source.endpoint is None:
                return None, {}
            mappings = list(
                session.scalars(
                    select(SourceTagMapping).where(
                        SourceTagMapping.data_source_id == source.id,
                        SourceTagMapping.organization_id == source.organization_id,
                    )
                )
            )
            detached: GatewaySource = {
                "id": source.id,
                "organization_id": source.organization_id,
                "source_type": source.source_type,
                "endpoint": source.endpoint,
                "configuration": dict(source.configuration),
            }
            map_values = {item.source_key: (item.source_key, item.source_unit) for item in mappings}
        return detached, map_values

    def _handle_message(self, source_id: UUID, message: TelemetryMessage) -> None:
        with SessionLocal() as session:
            source = session.get(DataSource, source_id)
            if source is None or not source.read_only:
                return
            try:
                result = self._gateway.ingest(session, source, message)
                session.commit()
                if not result.get("accepted"):
                    logger.warning(
                        "Live observation not inserted; source_id=%s reason=%s",
                        source_id,
                        result.get("reason"),
                    )
            except Exception as exc:
                session.rollback()
                source = session.get(DataSource, source_id)
                if source is not None:
                    source.status = "ERROR"
                    source.error_count += 1
                    source.last_error = f"{type(exc).__name__}: {str(exc)[:350]}"
                    session.commit()
                logger.warning(
                    "Live telemetry rejected; source_id=%s reason=%s", source_id, type(exc).__name__
                )

    async def _report_health(self, source_id: UUID, monitor: Any) -> None:
        while not self._stop.is_set():
            health: SourceHealth = monitor.snapshot()
            with SessionLocal() as session:
                source = session.get(DataSource, source_id)
                if source is None:
                    return
                if source.last_error is None:
                    source.status = health.status
                source.last_message_at = health.last_message_at or source.last_message_at
                source.last_success_at = health.last_success_at or source.last_success_at
                source.message_rate_per_minute = health.message_rate_per_minute
                source.latency_ms = (
                    health.latency_ms if health.latency_ms is not None else source.latency_ms
                )
                source.error_count = max(source.error_count, health.error_count)
                source.freshness_s = health.freshness_s
                if health.last_error:
                    source.last_error = health.last_error
                elif health.status == "CONNECTED":
                    source.last_error = None
                session.commit()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.reporter_interval_s)
            except TimeoutError:
                pass

    async def _run_source(self, source_id: UUID) -> None:
        delay = 1.0
        while not self._stop.is_set():
            source, mappings = self._load_source(source_id)
            if source is None:
                return
            try:
                if source["source_type"] == "MQTT":
                    await self._run_mqtt(source_id, source, mappings)
                elif source["source_type"] == "OPCUA":
                    await self._run_opcua(source_id, source, mappings)
                else:
                    raise ValueError(
                        "Gateway supports only configured MQTT or OPC-UA read-only sources"
                    )
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._set_error(source_id, exc)
                logger.warning(
                    "Connector stopped; source_id=%s reason=%s", source_id, type(exc).__name__
                )
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
                delay = min(60.0, delay * 2)

    async def _run_mqtt(
        self,
        source_id: UUID,
        source: GatewaySource,
        mappings: dict[str, tuple[str, str]],
    ) -> None:
        endpoint = urlsplit(source["endpoint"])
        subscriber = MqttReadOnlySubscriber(
            endpoint.hostname or "",
            port=int(source["configuration"].get("mqtt_port") or endpoint.port or 1883),
            topics=tuple(mappings),
            on_message=lambda message: self._handle_message(source_id, message),
            client_id=(
                f"{str(source['configuration'].get('mqtt_client_id', 'processtwin-readonly'))}"
                f"-{source_id.hex[:12]}"
            )[:96],
            tls=endpoint.scheme == "mqtts",
            stale_after_s=float(source["configuration"].get("stale_after_s", 30.0)),
        )
        reporter = asyncio.create_task(self._report_health(source_id, subscriber.monitor))
        try:
            await asyncio.to_thread(subscriber.start, max_retries=3, base_backoff_s=0.5)
            await self._stop.wait()
        finally:
            subscriber.stop()
            reporter.cancel()
            await asyncio.gather(reporter, return_exceptions=True)

    async def _run_opcua(
        self,
        source_id: UUID,
        source: GatewaySource,
        mappings: dict[str, tuple[str, str]],
    ) -> None:
        connector = OpcUaReadOnlyConnector(
            source["endpoint"],
            stale_after_s=float(source["configuration"].get("stale_after_s", 30.0)),
        )
        reporter = asyncio.create_task(self._report_health(source_id, connector.monitor))
        try:
            await connector.connect()
            await connector.subscribe(
                {node_id: mapping for node_id, mapping in mappings.items()},
                lambda message: self._handle_message_async(source_id, message),
                sampling_interval_ms=int(
                    source["configuration"].get("opcua_sampling_interval_ms", 1000)
                ),
            )
            await self._stop.wait()
        finally:
            await connector.disconnect()
            reporter.cancel()
            await asyncio.gather(reporter, return_exceptions=True)

    async def _handle_message_async(self, source_id: UUID, message: TelemetryMessage) -> None:
        await asyncio.to_thread(self._handle_message, source_id, message)

    @staticmethod
    def _set_error(source_id: UUID, error: BaseException) -> None:
        with SessionLocal() as session:
            source = session.get(DataSource, source_id)
            if source is not None:
                source.status = "ERROR"
                source.error_count += 1
                source.last_error = f"{type(error).__name__}: {str(error)[:350]}"
                session.commit()


async def run_gateway() -> None:
    runtime = GatewayRuntime()
    try:
        await runtime.run()
    finally:
        runtime.stop()
