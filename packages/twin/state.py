"""Combine a measured state and CSTR model without hiding data provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from packages.physics import CSTRInputs, CSTRPhysicsModel, CSTRState
from packages.schemas import MeasurementSource, TwinStateValue
from packages.units import convert


@dataclass(frozen=True)
class TwinSnapshot:
    timestamp: datetime
    temperature: TwinStateValue
    pressure: TwinStateValue
    flow: TwinStateValue
    conversion: TwinStateValue
    yield_b: TwinStateValue
    selectivity_b: TwinStateValue
    heat_generation: TwinStateValue
    heat_removal: TwinStateValue
    health_status: str
    divergence_temperature_k: float | None


class DigitalTwinService:
    """Creates a state snapshot and labels measured, estimated and model-derived values."""

    def __init__(
        self, physics: CSTRPhysicsModel | None = None, divergence_limit_k: float = 10.0
    ) -> None:
        self.physics = physics or CSTRPhysicsModel()
        self.divergence_limit_k = divergence_limit_k

    def snapshot(
        self,
        *,
        state: CSTRState,
        inputs: CSTRInputs,
        timestamp: datetime | None = None,
        measured_temperature_k: float | None = None,
        measured_pressure_pa: float | None = None,
        measured_flow_m3_s: float | None = None,
    ) -> TwinSnapshot:
        now = timestamp or datetime.now(UTC)
        metrics = self.physics.metrics(state, inputs)
        divergence = (
            abs(measured_temperature_k - state.temperature_k)
            if measured_temperature_k is not None
            else None
        )
        health = (
            "DIVERGENT"
            if divergence is not None and divergence > self.divergence_limit_k
            else "HEALTHY"
        )

        def value(number: float, unit: str, source: MeasurementSource) -> TwinStateValue:
            return TwinStateValue(value=number, unit=unit, source=source, timestamp=now)

        return TwinSnapshot(
            timestamp=now,
            temperature=value(
                convert(measured_temperature_k, "K", "degC")
                if measured_temperature_k is not None
                else convert(state.temperature_k, "K", "degC"),
                "degC",
                MeasurementSource.MEASURED
                if measured_temperature_k is not None
                else MeasurementSource.ESTIMATED,
            ),
            pressure=value(
                convert(measured_pressure_pa, "Pa", "bar")
                if measured_pressure_pa is not None
                else convert(inputs.pressure_pa, "Pa", "bar"),
                "bar",
                MeasurementSource.MEASURED
                if measured_pressure_pa is not None
                else MeasurementSource.ESTIMATED,
            ),
            flow=value(
                convert(measured_flow_m3_s, "m3/s", "m3/h")
                if measured_flow_m3_s is not None
                else convert(inputs.feed_flow_m3_s, "m3/s", "m3/h"),
                "m3/h",
                MeasurementSource.MEASURED
                if measured_flow_m3_s is not None
                else MeasurementSource.ESTIMATED,
            ),
            conversion=value(metrics.conversion * 100, "%", MeasurementSource.ESTIMATED),
            yield_b=value(metrics.yield_b * 100, "%", MeasurementSource.ESTIMATED),
            selectivity_b=value(metrics.selectivity_b * 100, "%", MeasurementSource.ESTIMATED),
            heat_generation=value(
                metrics.heat_generation_w / 1000, "kW", MeasurementSource.ESTIMATED
            ),
            heat_removal=value(metrics.heat_removal_w / 1000, "kW", MeasurementSource.ESTIMATED),
            health_status=health,
            divergence_temperature_k=divergence,
        )
