from __future__ import annotations

from datetime import datetime
from typing import Protocol


class OpcUaDataSource(Protocol):
    """Read-only acquisition boundary for customer-configured OPC UA endpoints."""

    async def read_value(self, node_id: str) -> tuple[datetime, float, str]: ...


class OpcUaConnectorDisabled:
    """Explicit local-development state—not a claim of PLC connectivity."""

    async def read_value(self, node_id: str) -> tuple[datetime, float, str]:
        raise RuntimeError(f"OPC UA connector is not configured; cannot read {node_id}")
