"""Deterministic synthetic CSTR data with declared disturbances and sensor imperfections."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from packages.physics import CSTRInputs, CSTRPhysicsModel
from packages.units import convert


@dataclass(frozen=True)
class SimulatedPoint:
    timestamp: datetime
    tag: str
    value: float
    unit: str
    quality: str


class SyntheticCSTRPlant:
    """A disturbance schedule, physics trajectory and imperfect independent sensor observations."""

    def __init__(self, seed: int = 7, model: CSTRPhysicsModel | None = None) -> None:
        self.random = np.random.default_rng(seed)
        self.model = model or CSTRPhysicsModel()

    def inputs_at(self, seconds: float) -> CSTRInputs:
        hours = seconds / 3600.0
        # Days 1-7: normal; temperature variation; feed increase; cooling degradation; composition change; sensor drift; recovery.
        flow = 0.020 * (1.08 if 48 <= hours < 72 else 1.0)
        concentration = 100.0 * (1.07 if 96 <= hours < 120 else 1.0)
        cooling_temperature = 293.15 + (4.0 if 72 <= hours < 96 else 0.0)
        inlet_temperature = 453.15 + (2.0 * np.sin(hours * np.pi / 12) if 24 <= hours < 48 else 0.0)
        return CSTRInputs(
            feed_flow_m3_s=flow,
            feed_temperature_k=inlet_temperature,
            feed_concentration_a_mol_m3=concentration,
            cooling_temperature_k=cooling_temperature,
            pressure_pa=1_010_000.0 + 8_000.0 * np.sin(hours * np.pi / 4),
        )

    def history(
        self, hours: float = 168.0, interval_s: int = 300, start: datetime | None = None
    ) -> Iterator[SimulatedPoint]:
        if hours <= 0 or interval_s <= 0:
            raise ValueError("hours and interval must be positive")
        start_time = start or (datetime.now(UTC) - timedelta(hours=hours))
        initial_inputs = self.inputs_at(0.0)
        initial_state = self.model.steady_state(initial_inputs)
        trajectory = self.model.simulate(
            initial_state,
            self.inputs_at,
            (0.0, hours * 3600),
            sample_count=int(hours * 3600 / interval_s) + 1,
        )
        for second, state, metrics in zip(
            trajectory.time_s, trajectory.states, trajectory.metrics, strict=True
        ):
            timestamp = start_time + timedelta(seconds=float(second))
            inputs = self.inputs_at(float(second))
            values = {
                "REACTOR_TEMPERATURE": (convert(state.temperature_k, "K", "degC"), "degC", 0.18),
                "REACTOR_PRESSURE": (convert(inputs.pressure_pa, "Pa", "bar"), "bar", 0.025),
                "FEED_FLOW": (convert(inputs.feed_flow_m3_s, "m3/s", "m3/h"), "m3/h", 0.16),
                "FEED_CONCENTRATION": (inputs.feed_concentration_a_mol_m3, "mol/m3", 0.45),
                "COOLING_FLOW": (72.0 * (0.9 if 72 <= second / 3600 < 96 else 1.0), "m3/h", 0.2),
                "AGITATOR_SPEED": (180.0, "rpm", 0.5),
                "CONVERSION": (metrics.conversion * 100, "%", 0.15),
                "YIELD": (metrics.yield_b * 100, "%", 0.15),
                "SELECTIVITY": (metrics.selectivity_b * 100, "%", 0.12),
                "ENERGY_CONSUMPTION": (metrics.heat_removal_w / 1000, "kW", 2.0),
            }
            for tag, (actual, unit, noise) in values.items():
                # One-percent communication gaps are materialized as quality events by the seeder instead of fake values.
                if self.random.random() < 0.01:
                    continue
                drift = 0.0
                if tag == "REACTOR_TEMPERATURE" and 120 <= second / 3600 < 144:
                    drift = (second / 3600 - 120) * 0.05
                observed = actual + drift + self.random.normal(0.0, noise)
                quality = "GOOD"
                if self.random.random() < 0.002:
                    observed += 12 * noise
                    quality = "SUSPECT"
                yield SimulatedPoint(timestamp, tag, float(observed), unit, quality)
