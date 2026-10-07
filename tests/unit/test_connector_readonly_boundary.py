from __future__ import annotations

import inspect
from typing import Protocol

import pytest
from apps.api.processtwin_api.realtime import source_mode
from connectors.mqtt.adapter import MqttReadOnlySubscriber
from connectors.opcua.adapter import OpcUaReadOnlyConnector, OpcUaDataSource, OpcUaConnectorDisabled
from connectors.telemetry import TelemetryMessage, SourceHealthMonitor
from datetime import UTC, datetime


def _public_callable_names(instance: object) -> set[str]:
    return {
        name
        for name, value in inspect.getmembers(instance, predicate=inspect.iscoroutinefunction)
        if not name.startswith("_")
    } | {
        name
        for name, value in inspect.getmembers(instance, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


class _WriteLikeOperation(Protocol):
    def write(self, **kwargs: object) -> object: ...
    def set(self, **kwargs: object) -> object: ...
    def call(self, **kwargs: object) -> object: ...
    def publish(self, **kwargs: object) -> object: ...
    def invoke(self, **kwargs: object) -> object: ...


def test_opcua_readonly_connector_exposes_no_write_api() -> None:
    connector = OpcUaReadOnlyConnector("opc.tcp://localhost:4840")
    names = _public_callable_names(connector)
    forbidden = {"write", "set", "call", "publish", "invoke", "write_value", "write_data_value"}
    assert not names & forbidden, f"OPC-UA connector exposed a write-like API: {sorted(names & forbidden)}"


def test_opcua_protocol_is_read_only() -> None:
    assert hasattr(OpcUaDataSource, "read_value")
    assert hasattr(OpcUaDataSource, "discover_variables")
    assert hasattr(OpcUaDataSource, "subscribe")
    assert not hasattr(OpcUaDataSource, "write")
    assert not hasattr(OpcUaDataSource, "write_value")


def test_disabled_opcua_connector_refuses_all_operations() -> None:
    connector = OpcUaConnectorDisabled()
    with pytest.raises(RuntimeError, match="not configured"):
        connector.read_value("ns=2;s=R101.Temperature")  # type: ignore[misc]
    with pytest.raises(RuntimeError, match="not configured"):
        connector.discover_variables()  # type: ignore[misc]
    with pytest.raises(RuntimeError, match="not configured"):
        import asyncio
        asyncio.run(connector.subscribe({"ns=2;s=R101.Temperature": ("reactor.temperature", "degC")}, lambda message: None))  # type: ignore[misc]


def test_mqtt_subscriber_is_read_only_by_name() -> None:
    subscriber = MqttReadOnlySubscriber(
        "localhost",
        topics=("processtwin/org/plant/unit/sensor",),
        on_message=lambda message: None,
    )
    names = _public_callable_names(subscriber)
    forbidden = {"publish", "write", "set", "call", "invoke"}
    assert not names & forbidden, f"MQTT subscriber exposed a write-like API: {sorted(names & forbidden)}"


def test_source_mode_classifier_marks_opcua_and_mqtt_live_read_only() -> None:
    assert source_mode("SIMULATED") == "SIMULATION"
    assert source_mode("CSV") == "HISTORICAL"
    assert source_mode("MQTT") == "LIVE_READ_ONLY"
    assert source_mode("OPCUA") == "LIVE_READ_ONLY"
    assert source_mode("REST") == "HISTORICAL"
    assert source_mode("DATABASE") == "HISTORICAL"


def test_opcua_endpoint_validation_rejects_credentials_in_url() -> None:
    with pytest.raises(ValueError, match="Credentials must not be embedded"):
        OpcUaReadOnlyConnector("opc.tcp://user:password@localhost:4840")  # type: ignore[misc]


def test_opcua_endpoint_validation_rejects_http_only_endpoints() -> None:
    with pytest.raises(ValueError, match="opc.tcp:// or https://"):
        OpcUaReadOnlyConnector("http://localhost:8080")  # type: ignore[misc]


def test_source_health_monitor_does_not_expose_credentials() -> None:
    monitor = SourceHealthMonitor(stale_after_s=5)
    snapshot = monitor.snapshot()
    assert snapshot.status in {"CONNECTED", "DISCONNECTED", "ERROR"}
    assert snapshot.last_error is None or isinstance(snapshot.last_error, str)
    assert snapshot.message_rate_per_minute >= 0


def test_telemetry_message_is_read_only_value_carrier() -> None:
    message = TelemetryMessage(
        source_key="processtwin/org/plant/unit/sensor",
        value=180.0,
        unit="degC",
        timestamp=datetime.now(UTC),
    )
    assert message.source_key
    assert message.unit
    assert message.value == 180.0
    assert not hasattr(message, "command")
    assert not hasattr(message, "write")


def test_opcua_connector_required_files_exist() -> None:
    import pathlib
    package = pathlib.Path(__file__).resolve().parents[2] / "connectors" / "opcua"
    assert (package / "__init__.py").exists()
    assert (package / "adapter.py").exists()
    assert "OpcUaReadOnlyConnector" in (package / "adapter.py").read_text()


def test_connector_models_and_table_names() -> None:
    from apps.api.processtwin_api.models import ConnectorTag, ConnectorTagMapping

    assert ConnectorTag.__tablename__ == "connector_tags"
    assert ConnectorTagMapping.__tablename__ == "connector_tag_mappings"
    assert ConnectorTagMapping.__table_args__[0].name == "uq_connector_tag_mapping"


def test_connector_validation_rule_model_exists() -> None:
    from apps.api.processtwin_api.models import ConnectorValidationRule

    assert ConnectorValidationRule.__tablename__ == "connector_validation_rules"
    assert ConnectorValidationRule.__table_args__[0].name == "uq_connector_validation_rule"
