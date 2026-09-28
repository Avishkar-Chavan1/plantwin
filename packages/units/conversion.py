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


UNIT_ALIASES = {
    "°C": "degC",
    "ºC": "degC",
    "c": "degC",
    "C": "degC",
    "degC": "degC",
    "k": "K",
    "K": "K",
    "pa": "Pa",
    "Pa": "Pa",
    "kpa": "kPa",
    "kPa": "kPa",
    "bar": "bar",
    "mbar": "mbar",
    "m3/s": "m3/s",
    "m^3/s": "m3/s",
    "m3/h": "m3/h",
    "m^3/h": "m3/h",
    "kg/s": "kg/s",
    "kg/h": "kg/h",
    "g/s": "g/s",
    "g/h": "g/h",
    "mol/m3": "mol/m3",
    "mol/m^3": "mol/m3",
    "mol/l": "mol/L",
    "mol/L": "mol/L",
    "l/min": "L/min",
    "L/min": "L/min",
    "m3/min": "m3/min",
    "m^3/min": "m3/min",
    "w": "W",
    "W": "W",
    "kw": "kW",
    "kW": "kW",
    "j": "J",
    "J": "J",
    "kj": "kJ",
    "kJ": "kJ",
    "j/kg-k": "J/kg-K",
    "J/kg-K": "J/kg-K",
    "j/kg/k": "J/kg-K",
    "kj/kg-k": "kJ/kg-K",
    "kJ/kg-K": "kJ/kg-K",
    "kj/kg/k": "kJ/kg-K",
    "rpm": "rpm",
    "rad/s": "rad/s",
    "%": "%",
    "1": "1",
}


UNITS: dict[str, UnitDefinition] = {
    "K": UnitDefinition("temperature", 1.0),
    "degC": UnitDefinition("temperature", 1.0, 273.15),
    "Pa": UnitDefinition("pressure", 1.0),
    "kPa": UnitDefinition("pressure", 1000.0),
    "bar": UnitDefinition("pressure", 100_000.0),
    "mbar": UnitDefinition("pressure", 100.0),
    "m3/s": UnitDefinition("volumetric_flow", 1.0),
    "m3/h": UnitDefinition("volumetric_flow", 1 / 3600),
    "m3/min": UnitDefinition("volumetric_flow", 1 / 60),
    "L/min": UnitDefinition("volumetric_flow", 1 / 60_000),
    "kg/s": UnitDefinition("mass_flow", 1.0),
    "kg/h": UnitDefinition("mass_flow", 1 / 3600),
    "g/s": UnitDefinition("mass_flow", 1 / 1000),
    "g/h": UnitDefinition("mass_flow", 1 / 3_600_000),
    "mol/m3": UnitDefinition("concentration", 1.0),
    "mol/L": UnitDefinition("concentration", 1000.0),
    "kg/m3": UnitDefinition("density", 1.0),
    "W": UnitDefinition("power", 1.0),
    "kW": UnitDefinition("power", 1000.0),
    "J": UnitDefinition("energy", 1.0),
    "kJ": UnitDefinition("energy", 1000.0),
    "kWh": UnitDefinition("energy", 3_600_000.0),
    "J/kg-K": UnitDefinition("heat_capacity", 1.0),
    "kJ/kg-K": UnitDefinition("heat_capacity", 1000.0),
    "rpm": UnitDefinition("rotational_speed", 2 * 3.141592653589793 / 60),
    "rad/s": UnitDefinition("rotational_speed", 1.0),
    "%": UnitDefinition("dimensionless", 0.01),
    "1": UnitDefinition("dimensionless", 1.0),
}


def _canonical_unit(name: str) -> str:
    key = name.strip()
    normalized = UNIT_ALIASES.get(key)
    if normalized is not None:
        return normalized
    return key


def _unit(name: str) -> UnitDefinition:
    try:
        return UNITS[_canonical_unit(name)]
    except KeyError as exc:
        raise ValueError(f"Unsupported unit: {name}") from exc


def to_si(value: float, unit: str) -> float:
    """Convert an external value to its SI representation."""
    return _unit(unit).to_si(value)


def si_unit(unit: str) -> str:
    """Return the canonical SI unit used for the dimension of ``unit``."""
    definition = _unit(unit)
    si_units = {
        "temperature": "K",
        "pressure": "Pa",
        "volumetric_flow": "m3/s",
        "mass_flow": "kg/s",
        "concentration": "mol/m3",
        "density": "kg/m3",
        "power": "W",
        "energy": "J",
        "heat_capacity": "J/kg-K",
        "rotational_speed": "rad/s",
        "dimensionless": "1",
    }
    return si_units[definition.dimension]


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert only between compatible, known units."""
    source, target = _unit(from_unit), _unit(to_unit)
    if source.dimension != target.dimension:
        raise ValueError(f"Cannot convert {source.dimension} to {target.dimension}")
    return target.from_si(source.to_si(value))
