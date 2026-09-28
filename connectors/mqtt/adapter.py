from __future__ import annotations

import json
from dataclasses import dataclass

from apps.api.processtwin_api.contracts import ReadingRequest


@dataclass(frozen=True)
class MqttTopic:
    organization: str
    plant: str
    equipment: str
    sensor: str


class MqttReadingAdapter:
    """Topic/payload validation; broker connectivity is optional and never trusted."""

    def parse(self, topic: str, payload: bytes, sensor_id: str) -> ReadingRequest:
        parts = topic.split("/")
        if len(parts) != 5 or parts[0] != "processtwin" or not all(parts[1:]):
            raise ValueError(
                "MQTT topic must be processtwin/{organization}/{plant}/{equipment}/{sensor}"
            )
        try:
            body = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("MQTT payload must be JSON") from exc
        return ReadingRequest.model_validate(
            {
                "sensor_id": sensor_id,
                "timestamp": body.get("timestamp"),
                "value": body.get("value"),
                "unit": body.get("unit"),
            }
        )
