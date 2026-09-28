"""Explicit unit conversions at integration and display boundaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnitDefinition:
    dimension: str
    scale_to_si: float
    offset_to_si: float = 0.0

    def to_si(self, value: float) -> float:
        return value * self.scale_to_si + self.offset_to_si

    def from_si(self, value: float) -> float:
        return (value - self.offset_to_si) / self.scale_to_si


UNITS: dict[str, UnitDefinition] = {
    "K": UnitDefinition("temperature", 1.0),
    "degC": UnitDefinition("temperature", 1.0, 273.15),
    "Pa": UnitDefinition("pressure", 1.0),
    "bar": UnitDefinition("pressure", 100_000.0),
    "m3/s": UnitDefinition("volumetric_flow", 1.0),
    "m3/h": UnitDefinition("volumetric_flow", 1 / 3600),
    "kg/s": UnitDefinition("mass_flow", 1.0),
    "kg/h": UnitDefinition("mass_flow", 1 / 3600),
    "mol/m3": UnitDefinition("concentration", 1.0),
    "W": UnitDefinition("power", 1.0),
    "kW": UnitDefinition("power", 1000.0),
    "J": UnitDefinition("energy", 1.0),
    "kWh": UnitDefinition("energy", 3_600_000.0),
    "rpm": UnitDefinition("rotational_speed", 1.0),
}


def _unit(name: str) -> UnitDefinition:
    try:
        return UNITS[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported unit: {name}") from exc


def to_si(value: float, unit: str) -> float:
    """Convert an external value to its SI representation."""
    return _unit(unit).to_si(value)


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert only between compatible, known units."""
    source, target = _unit(from_unit), _unit(to_unit)
    if source.dimension != target.dimension:
        raise ValueError(f"Cannot convert {source.dimension} to {target.dimension}")
    return target.from_si(source.to_si(value))
