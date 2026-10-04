from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from math import isfinite
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import differential_evolution, least_squares  # type: ignore[import-untyped]

from packages.ml.pipeline import ModelMetrics, metrics
from packages.physics import CSTRInputs, CSTRParameters, CSTRPhysicsModel, CSTRState


@dataclass(frozen=True)
class CalibrationParameter:
    name: str
    value: float
    unit: str
    minimum: float
    maximum: float
    initial_value: float
    description: str
    source: str

    def validate(self) -> None:
        if not all(
            isfinite(value)
            for value in (self.value, self.minimum, self.maximum, self.initial_value)
        ):
            raise ValueError(f"Parameter {self.name} must use finite values")
        if self.minimum >= self.maximum:
            raise ValueError(f"Parameter {self.name} minimum must be lower than maximum")
        if not self.minimum <= self.initial_value <= self.maximum:
            raise ValueError(f"Parameter {self.name} initial value is outside its bounds")
        if not self.minimum <= self.value <= self.maximum:
            raise ValueError(f"Parameter {self.name} value is outside its bounds")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "value": self.value,
            "unit": self.unit,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "initial_value": self.initial_value,
            "description": self.description,
            "source": self.source,
        }


@dataclass(frozen=True)
class HistoricalCSTRSeries:
    timestamps: tuple[datetime, ...]
    feed_flow_m3_s: NDArray[np.float64]
    feed_temperature_k: NDArray[np.float64]
    feed_concentration_a_mol_m3: NDArray[np.float64]
    cooling_temperature_k: NDArray[np.float64]
    measured_temperature_k: NDArray[np.float64]
    pressure_pa: NDArray[np.float64] | None = None
    measured_concentration_a_mol_m3: NDArray[np.float64] | None = None
    measured_concentration_b_mol_m3: NDArray[np.float64] | None = None
    measured_concentration_c_mol_m3: NDArray[np.float64] | None = None
    feed_flow_source_unit: str = "m3/s"

    def __post_init__(self) -> None:
        count = len(self.timestamps)
        if count < 10:
            raise ValueError("At least 10 aligned GOOD observations are required")
        if any(
            right <= left for left, right in zip(self.timestamps, self.timestamps[1:], strict=False)
        ):
            raise ValueError("Calibration timestamps must be strictly increasing and unique")
        for name in (
            "feed_flow_m3_s",
            "feed_temperature_k",
            "feed_concentration_a_mol_m3",
            "cooling_temperature_k",
            "measured_temperature_k",
            "pressure_pa",
            "measured_concentration_a_mol_m3",
            "measured_concentration_b_mol_m3",
            "measured_concentration_c_mol_m3",
        ):
            values = getattr(self, name)
            if values is None:
                continue
            array = np.asarray(values, dtype=float)
            if array.shape != (count,) or not np.isfinite(array).all():
                raise ValueError(f"{name} must have {count} finite values")
            object.__setattr__(self, name, array)
        if np.any(self.feed_flow_m3_s < 0) or np.any(self.feed_concentration_a_mol_m3 < 0):
            raise ValueError("Feed flow and feed concentration cannot be negative")
        if np.any(self.feed_temperature_k <= 0) or np.any(self.cooling_temperature_k <= 0):
            raise ValueError("Feed and cooling temperatures must be positive Kelvin")
        if np.any(self.measured_temperature_k <= 0):
            raise ValueError("Measured temperatures must be positive Kelvin")

    @property
    def elapsed_seconds(self) -> NDArray[np.float64]:
        start = self.timestamps[0]
        return np.asarray([(stamp - start).total_seconds() for stamp in self.timestamps], dtype=np.float64)


@dataclass(frozen=True)
class EvaluationResult:
    metrics: dict[str, dict[str, float | None]]
    residual_distribution: dict[str, dict[str, float]]
    measured: dict[str, list[float]]
    predicted: dict[str, list[float]]
    residuals: dict[str, list[float]]
    observation_count: int


@dataclass(frozen=True)
class CalibrationResult:
    parameters: CSTRParameters
    initial_parameters: dict[str, float]
    calibrated_parameters: dict[str, float]
    bounds: dict[str, tuple[float, float]]
    objective: float
    optimizer: str
    optimizer_success: bool
    optimizer_message: str
    evaluations: dict[str, EvaluationResult]
    training_indices: tuple[int, int]
    validation_indices: tuple[int, int]
    test_indices: tuple[int, int]


_PARAMETER_SPECS: dict[str, tuple[str, str, float, float, str]] = {
    "k0_main_s": (
        "pre_exponential_factor_s",
        "s^-1",
        1e-6,
        1e9,
        "Desired reaction pre-exponential factor",
    ),
    "Ea_main_j_mol": (
        "activation_energy_j_mol",
        "J/mol",
        0.0,
        250_000.0,
        "Desired reaction activation energy",
    ),
    "deltaH_main_j_mol": (
        "reaction_enthalpy_j_mol",
        "J/mol",
        -500_000.0,
        500_000.0,
        "Desired reaction enthalpy",
    ),
    "k0_side_s": (
        "side_pre_exponential_factor_s",
        "s^-1",
        0.0,
        1e9,
        "Side reaction pre-exponential factor",
    ),
    "Ea_side_j_mol": (
        "side_activation_energy_j_mol",
        "J/mol",
        0.0,
        250_000.0,
        "Side reaction activation energy",
    ),
    "deltaH_side_j_mol": (
        "side_reaction_enthalpy_j_mol",
        "J/mol",
        -500_000.0,
        500_000.0,
        "Side reaction enthalpy",
    ),
    "U_w_m2_k": (
        "heat_transfer_coefficient_w_m2_k",
        "W/(m^2 K)",
        0.0,
        20_000.0,
        "Overall heat-transfer coefficient",
    ),
    "A_m2": ("heat_transfer_area_m2", "m^2", 1e-6, 1e5, "Effective heat-transfer area"),
    "V_m3": ("volume_m3", "m^3", 1e-6, 1e6, "Reactor working volume"),
    "rho_kg_m3": ("density_kg_m3", "kg/m^3", 1e-6, 20_000.0, "Process fluid density"),
    "Cp_j_kg_k": ("heat_capacity_j_kg_k", "J/(kg K)", 1e-6, 1e7, "Process fluid heat capacity"),
}
CALIBRATABLE_PARAMETERS = tuple(_PARAMETER_SPECS)


def parameter_values(parameters: CSTRParameters) -> dict[str, float]:
    return {
        external: float(getattr(parameters, attribute))
        for external, (attribute, _, _, _, _) in _PARAMETER_SPECS.items()
    }


def volumetric_flow_m3_s(
    values: NDArray[np.float64], normalized_unit: str, density_kg_m3: float
) -> NDArray[np.float64]:
    """Convert a normalized historian feed flow to the CSTR's volumetric-flow SI input."""
    flow = np.asarray(values, dtype=np.float64)
    if not np.isfinite(flow).all() or np.any(flow < 0):
        raise ValueError("Feed flow values must be finite and non-negative")
    if normalized_unit == "m3/s":
        return flow
    if normalized_unit == "kg/s":
        if not isfinite(density_kg_m3) or density_kg_m3 <= 0:
            raise ValueError("A finite positive fluid density is required to convert kg/s to m³/s")
        return flow / density_kg_m3
    raise ValueError("CSTR feed flow must be normalized to m3/s or kg/s")


def parameter_set_from_values(values: dict[str, Any]) -> CSTRParameters:
    defaults = CSTRParameters()
    changes: dict[str, float] = {}
    for external, record in values.items():
        if external not in _PARAMETER_SPECS:
            raise ValueError(f"Unsupported CSTR parameter: {external}")
        value = float(record["value"] if isinstance(record, dict) else record)
        attribute = _PARAMETER_SPECS[external][0]
        changes[attribute] = value
    return replace(defaults, **changes)


def parameter_catalog(parameters: CSTRParameters | None = None) -> dict[str, CalibrationParameter]:
    current = parameters or CSTRParameters()
    values = parameter_values(current)
    return {
        name: CalibrationParameter(
            name=name,
            value=values[name],
            unit=unit,
            minimum=minimum,
            maximum=maximum,
            initial_value=values[name],
            description=description,
            source="ProcessTwin reference-model default; not plant-calibrated",
        )
        for name, (_, unit, minimum, maximum, description) in _PARAMETER_SPECS.items()
    }


def validate_parameter_records(records: dict[str, dict[str, Any]]) -> CSTRParameters:
    changes: dict[str, float] = {}
    for name, record in records.items():
        if name not in _PARAMETER_SPECS:
            raise ValueError(f"Unsupported CSTR parameter: {name}")
        field = CalibrationParameter(name=name, **record)
        field.validate()
        _, _, physical_minimum, physical_maximum, _ = _PARAMETER_SPECS[name]
        if field.minimum < physical_minimum or field.maximum > physical_maximum:
            raise ValueError(f"Parameter {name} limits exceed its physically admissible range")
        changes[_PARAMETER_SPECS[name][0]] = field.value
    return replace(CSTRParameters(), **changes)


def _state_targets(series: HistoricalCSTRSeries) -> dict[str, NDArray[np.float64]]:
    targets = {"temperature_k": series.measured_temperature_k}
    for name, values in (
        ("concentration_a_mol_m3", series.measured_concentration_a_mol_m3),
        ("concentration_b_mol_m3", series.measured_concentration_b_mol_m3),
        ("concentration_c_mol_m3", series.measured_concentration_c_mol_m3),
    ):
        if values is not None:
            targets[name] = values
    return targets


def simulate_historical_series(
    series: HistoricalCSTRSeries, parameters: CSTRParameters
) -> dict[str, NDArray[np.float64]]:
    """Integrate the existing CSTR balances over observed historical input trajectories."""
    elapsed = series.elapsed_seconds
    if elapsed[-1] <= 0:
        raise ValueError("Historical calibration period must span a positive duration")
    pressure = series.pressure_pa
    pressure_values = pressure if pressure is not None else np.full(len(elapsed), 101_325.0)

    def inputs_at(seconds: float) -> CSTRInputs:
        return CSTRInputs(
            feed_flow_m3_s=float(np.interp(seconds, elapsed, series.feed_flow_m3_s)),
            feed_temperature_k=float(np.interp(seconds, elapsed, series.feed_temperature_k)),
            feed_concentration_a_mol_m3=float(
                np.interp(seconds, elapsed, series.feed_concentration_a_mol_m3)
            ),
            cooling_temperature_k=float(np.interp(seconds, elapsed, series.cooling_temperature_k)),
            pressure_pa=float(np.interp(seconds, elapsed, pressure_values)),
        )

    initial_a = (
        float(series.measured_concentration_a_mol_m3[0])
        if series.measured_concentration_a_mol_m3 is not None
        else float(series.feed_concentration_a_mol_m3[0])
    )
    initial_b = (
        float(series.measured_concentration_b_mol_m3[0])
        if series.measured_concentration_b_mol_m3 is not None
        else 0.0
    )
    initial_c = (
        float(series.measured_concentration_c_mol_m3[0])
        if series.measured_concentration_c_mol_m3 is not None
        else 0.0
    )
    initial = CSTRState(initial_a, initial_b, initial_c, float(series.measured_temperature_k[0]))
    result = CSTRPhysicsModel(parameters).simulate(
        initial,
        inputs_at,
        (0.0, float(elapsed[-1])),
        sample_times_s=elapsed,
    )
    return {
        "temperature_k": np.asarray([state.temperature_k for state in result.states]),
        "concentration_a_mol_m3": np.asarray(
            [state.concentration_a_mol_m3 for state in result.states]
        ),
        "concentration_b_mol_m3": np.asarray(
            [state.concentration_b_mol_m3 for state in result.states]
        ),
        "concentration_c_mol_m3": np.asarray(
            [state.concentration_c_mol_m3 for state in result.states]
        ),
    }


def _distribution(residual: NDArray[np.float64]) -> dict[str, float]:
    return {
        "mean": float(np.mean(residual)),
        "standard_deviation": float(np.std(residual, ddof=1)) if len(residual) > 1 else 0.0,
        "p05": float(np.quantile(residual, 0.05)),
        "p50": float(np.quantile(residual, 0.50)),
        "p95": float(np.quantile(residual, 0.95)),
        "minimum": float(np.min(residual)),
        "maximum": float(np.max(residual)),
    }


def evaluate_cstr(
    series: HistoricalCSTRSeries,
    parameters: CSTRParameters,
    indices: slice | None = None,
) -> EvaluationResult:
    prediction = simulate_historical_series(series, parameters)
    selection = indices or slice(0, len(series.timestamps))
    measured: dict[str, list[float]] = {}
    predicted: dict[str, list[float]] = {}
    residuals: dict[str, list[float]] = {}
    metric_values: dict[str, dict[str, float | None]] = {}
    distributions: dict[str, dict[str, float]] = {}
    for name, actual in _state_targets(series).items():
        observed = np.asarray(actual[selection], dtype=float)
        estimate = np.asarray(prediction[name][selection], dtype=float)
        residual = estimate - observed
        result: ModelMetrics = metrics(observed, estimate)
        metric_values[name] = {
            **result.as_dict(),
            "bias": float(np.mean(residual)),
        }
        distributions[name] = _distribution(residual)
        measured[name] = observed.tolist()
        predicted[name] = estimate.tolist()
        residuals[name] = residual.tolist()
    count = len(next(iter(measured.values()))) if measured else 0
    return EvaluationResult(metric_values, distributions, measured, predicted, residuals, count)


def calibrate_cstr(
    series: HistoricalCSTRSeries,
    initial_parameters: CSTRParameters,
    bounds: dict[str, tuple[float, float]],
    *,
    method: str = "least_squares",
    max_evaluations: int = 100,
) -> CalibrationResult:
    """Fit selected physical parameters on the chronological train segment only."""
    if not bounds:
        raise ValueError("Select at least one parameter to calibrate")
    if "U_w_m2_k" in bounds and "A_m2" in bounds:
        raise ValueError("U and A are not separately identifiable through UA; calibrate only one")
    if series.feed_flow_source_unit == "kg/s" and "rho_kg_m3" in bounds:
        raise ValueError(
            "Density cannot be calibrated independently when mass flow is converted to volumetric flow using the parameter-set density"
        )
    names = tuple(bounds)
    if any(name not in _PARAMETER_SPECS for name in names):
        raise ValueError("Calibration bounds contain an unsupported CSTR parameter")
    lower_values = [float(bounds[name][0]) for name in names]
    upper_values = [float(bounds[name][1]) for name in names]
    initial = parameter_values(initial_parameters)
    initial_values = [initial[name] for name in names]
    if not all(isfinite(value) for value in (*lower_values, *upper_values)) or any(
        lower >= upper for lower, upper in zip(lower_values, upper_values, strict=True)
    ):
        raise ValueError("Each calibration parameter needs finite increasing bounds")
    for name, (minimum, maximum) in bounds.items():
        if minimum < _PARAMETER_SPECS[name][2] or maximum > _PARAMETER_SPECS[name][3]:
            raise ValueError(
                f"Bounds for {name} exceed its physically admissible calibration range"
            )
    if any(
        value < lower or value > upper
        for value, lower, upper in zip(initial_values, lower_values, upper_values, strict=True)
    ):
        raise ValueError("Initial parameter values must lie within their calibration bounds")

    train_end = int(len(series.timestamps) * 0.6)
    validation_end = train_end + int(len(series.timestamps) * 0.2)
    train_slice = slice(0, train_end)
    targets = _state_targets(series)
    scales = {
        name: max(
            float(np.std(values[train_slice])),
            abs(float(np.mean(values[train_slice]))) * 0.01,
            1e-6,
        )
        for name, values in targets.items()
        if len(values[train_slice]) > 0
    }

    def parameters_at(vector: NDArray[np.float64]) -> CSTRParameters:
        changes = {
            _PARAMETER_SPECS[name][0]: float(value)
            for name, value in zip(names, vector, strict=True)
        }
        return replace(initial_parameters, **changes)

    def residual_vector(vector: NDArray[np.float64]) -> NDArray[np.float64]:
        predicted = simulate_historical_series(series, parameters_at(vector))
        residuals = [
            (predicted[name][train_slice] - values[train_slice]) / scales[name]
            for name, values in targets.items()
        ]
        return np.concatenate(residuals)

    if method == "least_squares":
        result = least_squares(
            residual_vector,
            np.asarray(initial_values, dtype=float),
            bounds=(np.asarray(lower_values, dtype=float), np.asarray(upper_values, dtype=float)),
            max_nfev=max_evaluations,
            loss="soft_l1",
            x_scale="jac",
            diff_step=1e-3,
        )
        fitted = result.x
        objective = float(np.mean(np.square(residual_vector(fitted))))
        success = bool(result.success)
        message = str(result.message)
    elif method == "differential_evolution":
        result = differential_evolution(
            lambda vector: float(np.mean(np.square(residual_vector(vector)))),
            list(zip(lower_values, upper_values, strict=True)),
            maxiter=max_evaluations,
            polish=True,
            seed=42,
            workers=1,
            updating="immediate",
        )
        fitted = result.x
        objective = float(result.fun)
        success = bool(result.success)
        message = str(result.message)
    else:
        raise ValueError("Calibration methods are least_squares and differential_evolution")

    calibrated = parameters_at(fitted)
    evaluation = {
        "train": evaluate_cstr(series, calibrated, slice(0, train_end)),
        "validation": evaluate_cstr(series, calibrated, slice(train_end, validation_end)),
        "test": evaluate_cstr(series, calibrated, slice(validation_end, len(series.timestamps))),
    }
    return CalibrationResult(
        calibrated,
        {name: initial[name] for name in names},
        {name: float(value) for name, value in zip(names, fitted, strict=True)},
        bounds,
        objective,
        method,
        success,
        message,
        evaluation,
        (0, train_end),
        (train_end, validation_end),
        (validation_end, len(series.timestamps)),
    )
