"""Bounded, repeatable physics optimization. Outputs remain advisory scenarios."""

from __future__ import annotations

from dataclasses import dataclass

from scipy.optimize import differential_evolution

from packages.physics import CSTRInputs, CSTRPhysicsModel


@dataclass(frozen=True)
class OperatingEnvelope:
    temperature_k_min: float = 443.15  # 170 C
    temperature_k_max: float = 463.15  # 190 C
    pressure_pa_min: float = 800_000.0
    pressure_pa_max: float = 1_200_000.0
    flow_m3_s_min: float = 0.016
    flow_m3_s_max: float = 0.024
    max_energy_w: float = 1_000_000.0

    def __post_init__(self) -> None:
        bounds = (
            (self.temperature_k_min, self.temperature_k_max),
            (self.pressure_pa_min, self.pressure_pa_max),
            (self.flow_m3_s_min, self.flow_m3_s_max),
        )
        if any(low >= high for low, high in bounds) or self.max_energy_w <= 0:
            raise ValueError("Operating envelope requires increasing, positive bounds")

    def contains(self, inputs: CSTRInputs) -> bool:
        return (
            self.temperature_k_min <= inputs.feed_temperature_k <= self.temperature_k_max
            and self.pressure_pa_min <= inputs.pressure_pa <= self.pressure_pa_max
            and self.flow_m3_s_min <= inputs.feed_flow_m3_s <= self.flow_m3_s_max
        )


@dataclass(frozen=True)
class OptimizationResult:
    baseline_yield: float
    optimized_yield: float
    baseline_energy_w: float
    optimized_energy_w: float
    inputs: CSTRInputs
    objective: float
    constraint_status: str
    advisory: str


class OptimizationService:
    def __init__(
        self, physics: CSTRPhysicsModel | None = None, envelope: OperatingEnvelope | None = None
    ) -> None:
        self.physics = physics or CSTRPhysicsModel()
        self.envelope = envelope or OperatingEnvelope()

    def evaluate(self, inputs: CSTRInputs) -> tuple[float, float]:
        state = self.physics.steady_state(inputs)
        metrics = self.physics.metrics(state, inputs)
        # External cooling-duty magnitude is a transparent energy proxy.
        return metrics.yield_b, metrics.heat_removal_w

    def optimize(self, baseline: CSTRInputs, energy_weight: float = 0.02) -> OptimizationResult:
        if not self.envelope.contains(baseline):
            raise ValueError("Baseline is outside the validated operating envelope")
        baseline_yield, baseline_energy = self.evaluate(baseline)
        bounds = [
            (self.envelope.temperature_k_min, self.envelope.temperature_k_max),
            (self.envelope.pressure_pa_min, self.envelope.pressure_pa_max),
            (self.envelope.flow_m3_s_min, self.envelope.flow_m3_s_max),
        ]

        def objective(values: list[float]) -> float:
            candidate = CSTRInputs(
                feed_temperature_k=values[0],
                pressure_pa=values[1],
                feed_flow_m3_s=values[2],
                feed_concentration_a_mol_m3=baseline.feed_concentration_a_mol_m3,
                cooling_temperature_k=baseline.cooling_temperature_k,
            )
            yield_b, energy = self.evaluate(candidate)
            energy_penalty = (
                max(0.0, energy - self.envelope.max_energy_w) / self.envelope.max_energy_w
            )
            return (
                -yield_b
                + energy_weight * (energy / self.envelope.max_energy_w)
                + 100.0 * energy_penalty
            )

        solved = differential_evolution(objective, bounds, seed=42, polish=True, workers=1)
        if not solved.success:
            raise RuntimeError(f"Optimization did not converge: {solved.message}")
        candidate = CSTRInputs(
            feed_temperature_k=float(solved.x[0]),
            pressure_pa=float(solved.x[1]),
            feed_flow_m3_s=float(solved.x[2]),
            feed_concentration_a_mol_m3=baseline.feed_concentration_a_mol_m3,
            cooling_temperature_k=baseline.cooling_temperature_k,
        )
        yield_b, energy = self.evaluate(candidate)
        status = (
            "PASS"
            if energy <= self.envelope.max_energy_w and self.envelope.contains(candidate)
            else "FAIL"
        )
        return OptimizationResult(
            baseline_yield=baseline_yield,
            optimized_yield=yield_b,
            baseline_energy_w=baseline_energy,
            optimized_energy_w=energy,
            inputs=candidate,
            objective=float(-solved.fun),
            constraint_status=status,
            advisory="AI-generated engineering recommendation. Verify against plant operating procedures before implementation.",
        )
