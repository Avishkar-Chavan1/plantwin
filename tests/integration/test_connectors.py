from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from connectors.csv import parse_csv_dataset, parse_csv_rows
from connectors.mqtt import MqttReadingAdapter
from connectors.opcua.adapter import OpcUaConnectorDisabled
from connectors.rest import RestReadingAdapter


def valid_reading(sensor_id: str) -> dict[str, object]:
    return {
        "sensor_id": sensor_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "value": 180.2,
        "unit": "degC",
    }


def test_rest_connector_uses_application_reading_contract() -> None:
    sensor_id = str(uuid4())
    reading = RestReadingAdapter().parse(valid_reading(sensor_id))
    assert str(reading.sensor_id) == sensor_id
    assert reading.value == 180.2


def test_csv_connector_rejects_missing_columns_and_accepts_valid_rows() -> None:
    with pytest.raises(ValueError, match="Required columns"):
        parse_csv_rows(b"timestamp,value\n2026-01-01T00:00:00Z,1\n")
    sensor_id = str(uuid4())
    raw = (
        "sensor_id,timestamp,value,unit\n"
        f"{sensor_id},2026-01-01T00:00:00Z,180.2,degC\n"
    ).encode()
    parsed = parse_csv_rows(raw)
    assert len(parsed) == 1
    assert parsed[0].unit == "degC"


def test_csv_connector_supports_arbitrary_plant_tags() -> None:
    raw = (
        b"timestamp,TI_101,PI_101,FI_101,AI_101\n"
        b"2026-01-01T00:00:00Z,180,10.1,72,2.5\n"
    )
    rows = parse_csv_dataset(
        raw,
        tag_mapping={
            "TI_101": {"name": "reactor.temperature", "unit": "degC"},
            "PI_101": {"name": "reactor.pressure", "unit": "bar"},
            "FI_101": {"name": "reactor.feed_flow", "unit": "kg/h"},
            "AI_101": {"name": "reactor.feed_concentration", "unit": "mol/L"},
        },
    )
    assert len(rows) == 1
    assert rows[0]["reactor.temperature"] == 180.0
    assert rows[0]["reactor.pressure"] == 10.1
    assert rows[0]["reactor.feed_flow"] == 72.0
    assert rows[0]["reactor.feed_concentration"] == 2.5


def test_mqtt_connector_validates_topic_and_json_payload() -> None:
    sensor_id = str(uuid4())
    adapter = MqttReadingAdapter()
    reading = adapter.parse(
        "processtwin/org/plant/R-101/temperature",
        b'{"timestamp":"2026-01-01T00:00:00Z","value":180.2,"unit":"degC"}',
        sensor_id,
    )
    assert str(reading.sensor_id) == sensor_id
    with pytest.raises(ValueError, match="topic"):
        adapter.parse("bad/topic", b"{}", sensor_id)
    with pytest.raises(ValueError, match="JSON"):
        adapter.parse("processtwin/org/plant/R-101/temperature", b"not-json", sensor_id)


@pytest.mark.asyncio
async def test_opcua_connector_is_explicitly_disabled_without_configuration() -> None:
    with pytest.raises(RuntimeError, match="not configured"):
        await OpcUaConnectorDisabled().read_value("ns=2;s=R101.Temperature")
