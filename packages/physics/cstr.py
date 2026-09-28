"""Dynamic, SI-unit model of a non-isothermal first-order parallel-reaction CSTR."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

GAS_CONSTANT_J_PER_MOL_K = 8.314462618


@dataclass(frozen=True)
class CSTRParameters:
    """Physical and kinetic values, with all fields expressed in SI units."""

    volume_m3: float = 5.0
    density_kg_m3: float = 1000.0
    heat_capacity_j_kg_k: float = 4000.0
    activation_energy_j_mol: float = 45_000.0
    pre_exponential_factor_s: float = 5_000.0
    reaction_enthalpy_j_mol: float = -70_000.0
    side_activation_energy_j_mol: float = 55_000.0
    side_pre_exponential_factor_s: float = 1_000.0
    side_reaction_enthalpy_j_mol: float = -45_000.0
    # Nominal UA=800 W/K is calibrated to the synthetic reference operating point near 180°C.
    heat_transfer_coefficient_w_m2_k: float = 40.0
    heat_transfer_area_m2: float = 20.0

    def __post_init__(self) -> None:
        positive = ("volume_m3", "density_kg_m3", "heat_capacity_j_kg_k")
        if any(getattr(self, name) <= 0.0 for name in positive):
            raise ValueError("Reactor volume, density and heat capacity must be positive")
        if self.pre_exponential_factor_s < 0.0 or self.side_pre_exponential_factor_s < 0.0:
            raise ValueError("Pre-exponential factors cannot be negative")

    @property
    def ua_w_k(self) -> float:
        return self.heat_transfer_coefficient_w_m2_k * self.heat_transfer_area_m2


@dataclass(frozen=True)
class CSTRInputs:
    """Manipulated and feed values at one integration instant, in SI units."""

    feed_flow_m3_s: float = 0.020
    feed_temperature_k: float = 453.15
    feed_concentration_a_mol_m3: float = 100.0
    cooling_temperature_k: float = 293.15
    pressure_pa: float = 1_010_000.0

    def __post_init__(self) -> None:
        if self.feed_flow_m3_s < 0.0:
            raise ValueError("Feed flow cannot be negative")
        if self.feed_temperature_k <= 0.0 or self.cooling_temperature_k <= 0.0:
            raise ValueError("Temperature must be expressed as positive Kelvin")
        if self.feed_concentration_a_mol_m3 < 0.0 or self.pressure_pa < 0.0:
            raise ValueError("Concentration and pressure cannot be negative")


@dataclass(frozen=True)
class CSTRState:
    """The integrated state vector in SI units."""

    concentration_a_mol_m3: float
    concentration_b_mol_m3: float
    concentration_c_mol_m3: float
    temperature_k: float

    def vector(self) -> np.ndarray:
        return np.array(
            [
                self.concentration_a_mol_m3,
                self.concentration_b_mol_m3,
                self.concentration_c_mol_m3,
                self.temperature_k,
            ],
            dtype=float,
        )

    @classmethod
    def from_vector(cls, vector: np.ndarray) -> CSTRState:
        return cls(*(float(value) for value in vector))


@dataclass(frozen=True)
class StateMetrics:
    conversion: float
    yield_b: float
    selectivity_b: float
    reaction_rate_b_mol_m3_s: float
    reaction_rate_c_mol_m3_s: float
    heat_generation_w: float
    heat_removal_w: float
    residence_time_s: float | None


@dataclass(frozen=True)
class SimulationResult:
    time_s: np.ndarray
    states: tuple[CSTRState, ...]
    metrics: tuple[StateMetrics, ...]

    @property
    def final_state(self) -> CSTRState:
        return self.states[-1]

    @property
    def final_metrics(self) -> StateMetrics:
        return self.metrics[-1]


InputFunction = Callable[[float], CSTRInputs]


class CSTRPhysicsModel:
    """Numerically integrates CSTR material and energy balances with solve_ivp."""

    def __init__(self, parameters: CSTRParameters | None = None) -> None:
        self.parameters = parameters or CSTRParameters()

    def rate_constants(self, temperature_k: float) -> tuple[float, float]:
        if temperature_k <= 0.0:
            raise ValueError("Arrhenius temperature must be positive Kelvin")
        p = self.parameters
        k_main = p.pre_exponential_factor_s * np.exp(
            -p.activation_energy_j_mol / (GAS_CONSTANT_J_PER_MOL_K * temperature_k)
        )
        k_side = p.side_pre_exponential_factor_s * np.exp(
            -p.side_activation_energy_j_mol / (GAS_CONSTANT_J_PER_MOL_K * temperature_k)
        )
        return float(k_main), float(k_side)

    def rates(self, state: CSTRState) -> tuple[float, float]:
        k_main, k_side = self.rate_constants(state.temperature_k)
        concentration_a = max(state.concentration_a_mol_m3, 0.0)
        return k_main * concentration_a, k_side * concentration_a

    def derivatives(self, _time_s: float, vector: np.ndarray, inputs: CSTRInputs) -> np.ndarray:
        """Return [dCA/dt, dCB/dt, dCC/dt, dT/dt] from the balances."""
        state = CSTRState.from_vector(vector)
        rate_main, rate_side = self.rates(state)
        p = self.parameters
        flow_over_volume = inputs.feed_flow_m3_s / p.volume_m3
        dca = flow_over_volume * (inputs.feed_concentration_a_mol_m3 - state.concentration_a_mol_m3)
        dca -= rate_main + rate_side
        dcb = -flow_over_volume * state.concentration_b_mol_m3 + rate_main
        dcc = -flow_over_volume * state.concentration_c_mol_m3 + rate_side
        reaction_heat_w = (
            -p.reaction_enthalpy_j_mol * rate_main - p.side_reaction_enthalpy_j_mol * rate_side
        ) * p.volume_m3
        cooling_heat_w = p.ua_w_k * (inputs.cooling_temperature_k - state.temperature_k)
        feed_heat_w = (
            p.density_kg_m3
            * p.heat_capacity_j_kg_k
            * inputs.feed_flow_m3_s
            * (inputs.feed_temperature_k - state.temperature_k)
        )
        thermal_mass_j_k = p.density_kg_m3 * p.heat_capacity_j_kg_k * p.volume_m3
        dtemp = (feed_heat_w + reaction_heat_w + cooling_heat_w) / thermal_mass_j_k
        return np.array([dca, dcb, dcc, dtemp], dtype=float)

    def metrics(self, state: CSTRState, inputs: CSTRInputs) -> StateMetrics:
        rate_main, rate_side = self.rates(state)
        p = self.parameters
        inlet = inputs.feed_concentration_a_mol_m3
        conversion = (
            max(0.0, min(1.0, 1.0 - state.concentration_a_mol_m3 / inlet)) if inlet else 0.0
        )
        yield_b = max(0.0, state.concentration_b_mol_m3 / inlet) if inlet else 0.0
        products = state.concentration_b_mol_m3 + state.concentration_c_mol_m3
        selectivity_b = state.concentration_b_mol_m3 / products if products > 1e-12 else 0.0
        heat_generation_w = (
            -p.reaction_enthalpy_j_mol * rate_main - p.side_reaction_enthalpy_j_mol * rate_side
        ) * p.volume_m3
        # Positive means heat is being removed from the reactor by the utility.
        heat_removal_w = p.ua_w_k * (state.temperature_k - inputs.cooling_temperature_k)
        residence_time_s = (
            p.volume_m3 / inputs.feed_flow_m3_s if inputs.feed_flow_m3_s > 0 else None
        )
        return StateMetrics(
            conversion=conversion,
            yield_b=yield_b,
            selectivity_b=selectivity_b,
            reaction_rate_b_mol_m3_s=rate_main,
            reaction_rate_c_mol_m3_s=rate_side,
            heat_generation_w=heat_generation_w,
            heat_removal_w=heat_removal_w,
            residence_time_s=residence_time_s,
        )

    def simulate(
        self,
        initial_state: CSTRState,
        inputs: CSTRInputs | InputFunction,
        time_span_s: tuple[float, float],
        *,
        sample_count: int = 121,
    ) -> SimulationResult:
        """Run a dynamic scenario; callable inputs permit scheduled disturbances."""
        if sample_count < 2 or time_span_s[1] <= time_span_s[0]:
            raise ValueError("Simulation needs at least two samples and an increasing time span")
        input_at = inputs if callable(inputs) else lambda _time: inputs
        evaluation_times = np.linspace(*time_span_s, sample_count)

        solution = solve_ivp(
            lambda time, vector: self.derivatives(time, vector, input_at(time)),
            time_span_s,
            initial_state.vector(),
            t_eval=evaluation_times,
            method="LSODA",
            rtol=1e-7,
            atol=1e-9,
        )
        if not solution.success:
            raise RuntimeError(f"CSTR integration failed: {solution.message}")
        states = tuple(
            CSTRState.from_vector(solution.y[:, index]) for index in range(solution.y.shape[1])
        )
        metrics = tuple(
            self.metrics(state, input_at(float(time)))
            for state, time in zip(states, solution.t, strict=True)
        )
        return SimulationResult(time_s=solution.t, states=states, metrics=metrics)

    def steady_state(self, inputs: CSTRInputs, guess: CSTRState | None = None) -> CSTRState:
        """Solve d(state)/dt = 0; rejects nonphysical or unconverged roots."""
        initial = guess or CSTRState(
            inputs.feed_concentration_a_mol_m3 / 2,
            inputs.feed_concentration_a_mol_m3 / 2,
            0.0,
            inputs.feed_temperature_k,
        )
        concentration_scale = max(inputs.feed_concentration_a_mol_m3, 1.0)
        temperature_scale = max(inputs.feed_temperature_k, 1.0)
        scales = np.array(
            [concentration_scale, concentration_scale, concentration_scale, temperature_scale]
        )
        result = least_squares(
            lambda vector: self.derivatives(0.0, vector, inputs) / scales,
            np.maximum(initial.vector(), [0.0, 0.0, 0.0, 1.0]),
            bounds=([0.0, 0.0, 0.0, 1.0], [np.inf, np.inf, np.inf, np.inf]),
            xtol=1e-11,
            ftol=1e-11,
            gtol=1e-11,
            max_nfev=5_000,
        )
        if not result.success or np.linalg.norm(result.fun, ord=np.inf) > 1e-7:
            raise RuntimeError("No physical CSTR steady state found for requested conditions")
        return CSTRState.from_vector(result.x)

    def with_parameters(self, **changes: float) -> CSTRPhysicsModel:
        return CSTRPhysicsModel(replace(self.parameters, **changes))
