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
    "write_attribute(",
    "write_data_value(",
    "set_attribute(",
    "add_nodes(",
    "delete_nodes(",
    "register_nodes(",
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


def test_every_mutating_route_is_database_only_and_authenticated() -> None:
    """POST routes may only change ProcessTwin's own records; none may reach a plant."""
    public_post = {"/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/logout"}
    for route in app.routes:
        methods = getattr(route, "methods", set()) or set()
        path = getattr(route, "path", "")
        if methods & {"PUT", "PATCH", "DELETE"}:
            raise AssertionError(f"unexpected mutating verb on {path}: {sorted(methods)}")
        if "POST" in methods and path not in public_post:
            assert getattr(route, "dependant", None) is not None
            assert route.dependant.dependencies, f"{path} must be authenticated"  # type: ignore[attr-defined]


def test_gateway_ingest_refuses_a_source_that_is_not_read_only() -> None:
    from types import SimpleNamespace

    from apps.api.processtwin_api.realtime import LiveIngestionGateway

    source = SimpleNamespace(read_only=False)
    with pytest.raises(ValueError, match="read-only"):
        LiveIngestionGateway().ingest(None, source, None)  # type: ignore[arg-type]


def test_connector_apis_always_create_read_only_sources() -> None:
    text = (PROJECT_ROOT / "apps" / "api" / "processtwin_api" / "connector_api.py").read_text(
        encoding="utf-8"
    )
    live = (PROJECT_ROOT / "apps" / "api" / "processtwin_api" / "live_api.py").read_text(
        encoding="utf-8"
    )
    for source in (text, live):
        assert "read_only=True" in source
        assert "read_only=False" not in source
