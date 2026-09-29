from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from packages.calibration import (
    HistoricalCSTRSeries,
    calibrate_cstr,
    evaluate_cstr,
    parameter_catalog,
    simulate_historical_series,
    validate_parameter_records,
    volumetric_flow_m3_s,
)
from packages.physics import CSTRInputs, CSTRParameters, CSTRPhysicsModel


def observed_series(parameters: CSTRParameters, count: int = 36) -> HistoricalCSTRSeries:
    timestamps = tuple(
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index) for index in range(count)
    )
    flow = np.full(count, 0.018)
    feed_temperature = np.full(count, 448.15)
    feed_concentration = np.full(count, 100.0)
    cooling_temperature = 288.15 + 8.0 * np.sin(np.linspace(0, 5 * np.pi, count))
    inputs = CSTRInputs(
        feed_flow_m3_s=float(flow[0]),
        feed_temperature_k=float(feed_temperature[0]),
        feed_concentration_a_mol_m3=float(feed_concentration[0]),
        cooling_temperature_k=float(cooling_temperature[0]),
    )
    initial = CSTRPhysicsModel(parameters).steady_state(inputs)
    truth = CSTRPhysicsModel(parameters).simulate(
        initial,
        lambda seconds: CSTRInputs(
            feed_flow_m3_s=float(np.interp(seconds, np.arange(count) * 3600, flow)),
            feed_temperature_k=float(np.interp(seconds, np.arange(count) * 3600, feed_temperature)),
            feed_concentration_a_mol_m3=float(
                np.interp(seconds, np.arange(count) * 3600, feed_concentration)
            ),
            cooling_temperature_k=float(
                np.interp(seconds, np.arange(count) * 3600, cooling_temperature)
            ),
        ),
        (0.0, float((count - 1) * 3600)),
        sample_times_s=np.arange(count) * 3600,
    )
    return HistoricalCSTRSeries(
        timestamps=timestamps,
        feed_flow_m3_s=flow,
        feed_temperature_k=feed_temperature,
        feed_concentration_a_mol_m3=feed_concentration,
        cooling_temperature_k=cooling_temperature,
        measured_temperature_k=np.asarray([state.temperature_k for state in truth.states]),
    )


def test_parameter_catalog_has_units_sources_and_bounded_values() -> None:
    catalog = parameter_catalog()
    assert {
        "k0_main_s",
        "Ea_main_j_mol",
        "deltaH_main_j_mol",
        "U_w_m2_k",
        "A_m2",
        "V_m3",
        "rho_kg_m3",
        "Cp_j_kg_k",
    } <= set(catalog)
    for item in catalog.values():
        item.validate()
        assert item.unit
        assert item.source
        assert item.minimum <= item.initial_value <= item.maximum


def test_cstr_fit_respects_bounds_and_improves_a_known_synthetic_case() -> None:
    true_parameters = CSTRParameters(heat_transfer_coefficient_w_m2_k=24.0)
    data = observed_series(true_parameters)
    initial = CSTRParameters(heat_transfer_coefficient_w_m2_k=40.0)
    result = calibrate_cstr(
        data,
        initial,
        {"U_w_m2_k": (10.0, 50.0)},
        method="least_squares",
        max_evaluations=40,
    )
    before = evaluate_cstr(data, initial, slice(*result.training_indices))
    after = result.evaluations["train"]
    assert result.optimizer_success
    assert 10.0 <= result.calibrated_parameters["U_w_m2_k"] <= 50.0
    assert result.calibrated_parameters["U_w_m2_k"] == pytest.approx(24.0, abs=0.5)
    assert after.metrics["temperature_k"]["rmse"] < before.metrics["temperature_k"]["rmse"]
    assert result.validation_indices == (21, 28)
    assert result.test_indices == (28, 36)


def test_calibration_rejects_nonphysical_or_nonidentifiable_bounds() -> None:
    data = observed_series(CSTRParameters())
    with pytest.raises(ValueError, match="physically admissible"):
        calibrate_cstr(data, CSTRParameters(), {"U_w_m2_k": (-1.0, 20.0)})
    with pytest.raises(ValueError, match="not separately identifiable"):
        calibrate_cstr(
            data,
            CSTRParameters(),
            {"U_w_m2_k": (20.0, 60.0), "A_m2": (10.0, 30.0)},
        )


def test_parameter_records_reject_limits_outside_physical_ranges() -> None:
    records = {name: item.as_dict() for name, item in parameter_catalog().items()}
    records["Ea_main_j_mol"]["minimum"] = -1.0
    with pytest.raises(ValueError, match="physically admissible"):
        validate_parameter_records(records)


def test_mass_flow_uses_versioned_density_at_the_cstr_volume_flow_boundary() -> None:
    assert volumetric_flow_m3_s(np.array([1.0, 2.0]), "kg/s", 1000.0) == pytest.approx(
        [0.001, 0.002]
    )
    assert volumetric_flow_m3_s(np.array([0.01]), "m3/s", 1000.0) == pytest.approx([0.01])
    with pytest.raises(ValueError, match="density"):
        volumetric_flow_m3_s(np.array([1.0]), "kg/s", 0.0)
    with pytest.raises(ValueError, match="m3/s or kg/s"):
        volumetric_flow_m3_s(np.array([1.0]), "mol/m3", 1000.0)


def test_evaluation_returns_measured_prediction_residual_bias_and_distribution() -> None:
    parameters = CSTRParameters()
    data = observed_series(parameters)
    result = evaluate_cstr(data, parameters, slice(28, 36))
    temperature_metrics = result.metrics["temperature_k"]
    assert result.observation_count == 8
    assert temperature_metrics["rmse"] == pytest.approx(0.0, abs=1e-5)
    assert temperature_metrics["bias"] == pytest.approx(0.0, abs=1e-5)
    assert len(result.measured["temperature_k"]) == 8
    assert len(result.predicted["temperature_k"]) == 8
    assert len(result.residuals["temperature_k"]) == 8
    assert {"p05", "p50", "p95"} <= set(result.residual_distribution["temperature_k"])


def test_irregular_sampling_is_simulated_at_observed_times() -> None:
    parameters = CSTRParameters()
    timestamps = tuple(
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)
        for seconds in (0, 20, 53, 110, 190, 310, 490, 750, 1100, 1600)
    )
    values = np.full(10, 453.15)
    series = HistoricalCSTRSeries(
        timestamps=timestamps,
        feed_flow_m3_s=np.full(10, 0.02),
        feed_temperature_k=values,
        feed_concentration_a_mol_m3=np.full(10, 100.0),
        cooling_temperature_k=np.full(10, 293.15),
        measured_temperature_k=values,
    )
    prediction = simulate_historical_series(series, parameters)
    assert prediction["temperature_k"].shape == (10,)
    with pytest.raises(ValueError, match="strictly increasing"):
        HistoricalCSTRSeries(
            timestamps=(timestamps[0], *timestamps[1:8], timestamps[7], timestamps[9]),
            feed_flow_m3_s=np.full(10, 0.02),
            feed_temperature_k=values,
            feed_concentration_a_mol_m3=np.full(10, 100.0),
            cooling_temperature_k=np.full(10, 293.15),
            measured_temperature_k=values,
        )
