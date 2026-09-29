"""Small, real baseline models for units not yet given a full non-isothermal implementation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.integrate import solve_ivp  # type: ignore[import-untyped]


@dataclass(frozen=True)
class BatchResult:
    time_s: np.ndarray
    concentration_a_mol_m3: np.ndarray


class BatchReactorPhysicsModel:
    """Isothermal first-order batch reactor: dCA/dt = -k CA."""

    def simulate(
        self, concentration_a0_mol_m3: float, rate_constant_s: float, duration_s: float
    ) -> BatchResult:
        if concentration_a0_mol_m3 < 0 or rate_constant_s < 0 or duration_s <= 0:
            raise ValueError("Invalid batch model inputs")
        times = np.linspace(0.0, duration_s, 100)
        result = solve_ivp(
            lambda _t, y: [-rate_constant_s * y[0]],
            (0.0, duration_s),
            [concentration_a0_mol_m3],
            t_eval=times,
        )
        if not result.success:
            raise RuntimeError(result.message)
        return BatchResult(times, result.y[0])


class PFRPhysicsModel:
    """Isothermal first-order PFR design relation, C_A,out=C_A,in exp(-k tau)."""

    def outlet_concentration(
        self, inlet_mol_m3: float, rate_constant_s: float, residence_time_s: float
    ) -> float:
        if min(inlet_mol_m3, rate_constant_s, residence_time_s) < 0:
            raise ValueError("PFR inputs cannot be negative")
        return float(inlet_mol_m3 * np.exp(-rate_constant_s * residence_time_s))
