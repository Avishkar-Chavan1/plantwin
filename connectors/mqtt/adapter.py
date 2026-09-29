from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from apps.api.processtwin_api.contracts import ReadingRequest
from connectors.telemetry import SourceHealth, SourceHealthMonitor, TelemetryMessage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MqttTopic:
    organization: str
    plant: str
    equipment: str
    sensor: str


class MqttReadingAdapter:
    """Strict source timestamp, engineering value and topic parsing for MQTT telemetry."""

    def parse(self, topic: str, payload: bytes, sensor_id: str) -> ReadingRequest:
        message = self.parse_message(topic, payload)
        return ReadingRequest.model_validate(
            {
                "sensor_id": sensor_id,
                "timestamp": message.timestamp,
                "value": message.value,
                "unit": message.unit,
            }
        )

    def parse_message(
        self, topic: str, payload: bytes, received_at: datetime | None = None
    ) -> TelemetryMessage:
        parts = topic.split("/")
        if len(parts) != 5 or parts[0] != "processtwin" or not all(parts[1:]):
            raise ValueError(
                "MQTT topic must be processtwin/{organization}/{plant}/{equipment}/{sensor}"
            )
        try:
            body = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("MQTT payload must be JSON") from exc
        if not isinstance(body, dict):
            raise ValueError("MQTT payload must be a JSON object")
        received = received_at or datetime.now(UTC)
        timestamp_value = body.get("timestamp")
        timestamp_source = "SOURCE"
        if timestamp_value is None:
            timestamp = received
            timestamp_source = "RECEIVED"
        elif isinstance(timestamp_value, (float, int)) and not isinstance(timestamp_value, bool):
            timestamp = datetime.fromtimestamp(float(timestamp_value), UTC)
        elif isinstance(timestamp_value, str):
            try:
                timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("MQTT timestamp must be ISO-8601 or Unix epoch seconds") from exc
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("MQTT timestamp must include a timezone")
            timestamp = timestamp.astimezone(UTC)
        else:
            raise ValueError("MQTT timestamp must be ISO-8601 or Unix epoch seconds")
        return TelemetryMessage.model_validate(
            {
                "source_key": topic,
                "timestamp": timestamp,
                "received_at": received,
                "timestamp_source": timestamp_source,
                "value": body.get("value"),
                "unit": body.get("unit"),
            }
        )


class MqttReadOnlySubscriber:
    """Read-only Paho subscriber. It never publishes or exposes a client to callers."""

    def __init__(
        self,
        broker_host: str,
        *,
        topics: tuple[str, ...],
        on_message: Callable[[TelemetryMessage], None],
        port: int = 1883,
        client_id: str = "processtwin-readonly",
        keepalive_s: int = 30,
        stale_after_s: float = 30.0,
        client_factory: Callable[[], Any] | None = None,
        tls: bool = False,
    ) -> None:
        if not broker_host or not topics or any(not topic for topic in topics):
            raise ValueError("MQTT broker host and non-empty subscription topics are required")
        self.broker_host = broker_host
        self.port = port
        self.topics = topics
        self.on_message = on_message
        self.client_id = client_id
        self.tls = tls
        self.keepalive_s = keepalive_s
        self.monitor = SourceHealthMonitor(stale_after_s=stale_after_s)
        self._factory = client_factory
        self._client: Any | None = None
        self._stop = threading.Event()

    @property
    def health(self) -> SourceHealth:
        return self.monitor.snapshot()

    def start(self, *, max_retries: int = 8, base_backoff_s: float = 0.5) -> None:
        if self._client is not None:
            return
        try:
            if self._factory is None:
                import paho.mqtt.client as mqtt

                client = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                    client_id=self.client_id,
                )
                if self.tls:
                    client.tls_set()
            else:
                client = self._factory()
            self._client = client
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            for attempt in range(max_retries + 1):
                if self._stop.is_set():
                    return
                try:
                    client.connect(self.broker_host, self.port, self.keepalive_s)
                    client.loop_start()
                    return
                except Exception as exc:
                    self.monitor.record_error(exc)
                    if attempt >= max_retries:
                        self._client = None
                        raise RuntimeError("MQTT connection retries exhausted") from exc
                    delay = min(30.0, base_backoff_s * (2**attempt))
                    time.sleep(delay + random.uniform(0, delay * 0.2))
        except ImportError as exc:
            raise RuntimeError("MQTT support requires the optional mqtt extra") from exc

    def stop(self) -> None:
        self._stop.set()
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            finally:
                self.monitor.set_connected(False)

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, reason_code: Any, *_: Any) -> None:
        if int(reason_code) != 0:
            self.monitor.record_error(f"Broker connection refused: {reason_code}")
            return
        self.monitor.set_connected(True)
        for topic in self.topics:
            client.subscribe(topic)

    def _on_disconnect(self, _client: Any, _userdata: Any, *args: Any) -> None:
        self.monitor.set_connected(False)
        if args and str(args[0]) not in {"0", "Success", "Normal disconnection"}:
            self.monitor.record_error(f"Broker disconnected: {args[0]}")

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        received = datetime.now(UTC)
        try:
            parsed = MqttReadingAdapter().parse_message(message.topic, message.payload, received)
            self.on_message(parsed)
            self.monitor.record_message(parsed, successful=True)
        except Exception as exc:
            if "parsed" in locals():
                self.monitor.record_message(parsed, successful=False)
            self.monitor.record_error(exc)
            logger.warning("MQTT message rejected; reason=%s", type(exc).__name__)
*** End Patch