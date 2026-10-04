"""Advisory-boundary regression tests: no code path may command plant equipment.

ProcessTwin is human-in-the-loop and read-only towards plant networks. These tests fail
if a connector, the gateway, or the API surface ever grows a write/control capability.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from apps.api.processtwin_api.main import app
from apps.gateway.processtwin_gateway.runtime import GatewayRuntime
from connectors.mqtt.adapter import MqttReadingAdapter, MqttReadOnlySubscriber
from connectors.opcua.adapter import (
    OpcUaConnectorDisabled,
    OpcUaDataSource,
    OpcUaReadOnlyConnector,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Route segments that would indicate plant control rather than advisory monitoring.
CONTROL_ROUTE_TERMS = ("control", "command", "actuator", "setpoint", "plc", "dcs", "scada")

# Public method names that would allow writing to a plant or broker.
CONTROL_METHODS = frozenset(
    {
        "write",
        "write_value",
        "write_values",
        "set_value",
        "call_method",
        "invoke",
        "publish",
        "send_command",
        "command",
        "control",
    }
)

# Source-level calls that must never appear in acquisition code.
CONTROL_CALLS = (
    ".publish(",
    "write_value(",
    "write_values(",
    "call_method(",
    "set_value(",
    "invoke(",
)

ACQUISITION_ROOTS = (PROJECT_ROOT / "connectors", PROJECT_ROOT / "apps" / "gateway")


def _public_members(cls: type[object]) -> set[str]:
    """Instance-visible member names across the MRO, ignoring private/dunder names."""
    names: set[str] = set()
    for klass in cls.__mro__:
        if klass is object:
            continue
        names.update(name for name in vars(klass) if not name.startswith("_"))
    return names


@pytest.mark.parametrize(
    "connector",
    [
        MqttReadOnlySubscriber,
        MqttReadingAdapter,
        OpcUaReadOnlyConnector,
        OpcUaConnectorDisabled,
        OpcUaDataSource,
        GatewayRuntime,
    ],
)
def test_connector_and_gateway_api_is_read_only(connector: type[object]) -> None:
    exposed = _public_members(connector) & CONTROL_METHODS
    assert not exposed, f"{connector.__name__} exposes control methods: {sorted(exposed)}"


def test_acquisition_code_never_calls_a_control_api() -> None:
    offenders: list[str] = []
    for root in ACQUISITION_ROOTS:
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for call in CONTROL_CALLS:
                if call in text:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {call}")
    assert offenders == [], f"control API usage found: {offenders}"


def test_openapi_exposes_no_control_route() -> None:
    paths = sorted(app.openapi()["paths"])
    offenders = [p for p in paths if any(term in p.lower() for term in CONTROL_ROUTE_TERMS)]
    assert offenders == [], f"control routes exposed: {offenders}"
