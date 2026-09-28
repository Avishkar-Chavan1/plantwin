from __future__ import annotations

import numpy as np
import pytest
from packages.physics import CSTRInputs, CSTRParameters, CSTRPhysicsModel, CSTRState


def isothermal_model(rate_constant_s: float) -> CSTRPhysicsModel:
    # k0=constant because Ea=0; zero enthalpy removes thermal feedback.
    return CSTRPhysicsModel(
        CSTRParameters(
            volume_m3=1.0,
            pre_exponential_factor_s=rate_constant_s,
            activation_energy_j_mol=0.0,
            side_pre_exponential_factor_s=0.0,
            reaction_enthalpy_j_mol=0.0,
            heat_transfer_coefficient_w_m2_k=0.0,
            heat_transfer_area_m2=1.0,
        )
    )


def test_first_order_steady_state_matches_analytical_cstr_conversion() -> None:
    rate_constant = 0.2
    flow = 0.1
    model = isothermal_model(rate_constant)
    inputs = CSTRInputs(feed_flow_m3_s=flow, feed_concentration_a_mol_m3=10.0)
    state = model.steady_state(inputs)
    numerical = model.metrics(state, inputs).conversion
    tau = model.parameters.volume_m3 / flow
    assert numerical == pytest.approx(rate_constant * tau / (1 + rate_constant * tau), abs=1e-6)


def test_zero_reaction_reaches_inlet_concentration_without_product() -> None:
    model = isothermal_model(0.0)
    inputs = CSTRInputs(feed_flow_m3_s=0.1, feed_concentration_a_mol_m3=25.0)
    result = model.simulate(
        CSTRState(0.0, 0.0, 0.0, inputs.feed_temperature_k), inputs, (0.0, 120.0)
    )
    assert result.final_state.concentration_a_mol_m3 == pytest.approx(25.0, abs=1e-3)
    assert result.final_state.concentration_b_mol_m3 == pytest.approx(0.0, abs=1e-9)
    assert result.final_metrics.conversion == pytest.approx(0.0, abs=1e-4)


def test_mass_balance_conserves_a_b_c_at_steady_state() -> None:
    model = CSTRPhysicsModel()
    inputs = CSTRInputs()
    state = model.steady_state(inputs)
    assert sum(
        (state.concentration_a_mol_m3, state.concentration_b_mol_m3, state.concentration_c_mol_m3)
    ) == pytest.approx(inputs.feed_concentration_a_mol_m3, rel=1e-6)


def test_arrhenius_rate_increases_with_temperature() -> None:
    model = CSTRPhysicsModel()
    cold, _ = model.rate_constants(350.0)
    hot, _ = model.rate_constants(450.0)
    assert hot > cold > 0.0


def test_residence_time_and_heat_removal_are_consistent() -> None:
    model = CSTRPhysicsModel(
        CSTRParameters(
            volume_m3=2.0, heat_transfer_coefficient_w_m2_k=100.0, heat_transfer_area_m2=3.0
        )
    )
    inputs = CSTRInputs(feed_flow_m3_s=0.5, cooling_temperature_k=300.0)
    metrics = model.metrics(CSTRState(1.0, 1.0, 0.0, 350.0), inputs)
    assert metrics.residence_time_s == 4.0
    assert metrics.heat_removal_w == 15_000.0


def test_zero_flow_has_no_residence_time_and_no_convective_change() -> None:
    model = isothermal_model(0.0)
    inputs = CSTRInputs(feed_flow_m3_s=0.0)
    state = CSTRState(12.0, 3.0, 1.0, inputs.feed_temperature_k)
    derivative = model.derivatives(0.0, state.vector(), inputs)
    assert np.allclose(derivative[:3], 0.0)
    assert model.metrics(state, inputs).residence_time_s is None


def test_temperature_input_must_be_kelvin() -> None:
    with pytest.raises(ValueError):
        CSTRInputs(feed_temperature_k=0.0)
