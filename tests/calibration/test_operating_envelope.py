from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from apps.api.processtwin_api.modeling import CalibrationSeriesError, _envelope_outside
from packages.calibration import HistoricalCSTRSeries


def sample_series() -> HistoricalCSTRSeries:
    count = 10
    return HistoricalCSTRSeries(
        timestamps=tuple(
            datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index)
            for index in range(count)
        ),
        feed_flow_m3_s=np.full(count, 0.02),
        feed_temperature_k=np.full(count, 453.15),
        feed_concentration_a_mol_m3=np.full(count, 100.0),
        cooling_temperature_k=np.full(count, 293.15),
        measured_temperature_k=np.full(count, 453.15),
        pressure_pa=np.full(count, 1_000_000.0),
    )


def test_envelope_supports_temperature_pressure_and_density_derived_mass_flow() -> None:
    envelope = {
        "temperature_c": {"minimum": 170.0, "maximum": 190.0},
        "pressure_bar": {"minimum": 8.0, "maximum": 12.0},
        "feed_flow_kg_h": {"minimum": 70_000.0, "maximum": 74_000.0},
    }
    assert _envelope_outside(sample_series(), envelope, density_kg_m3=1000.0) == {}


def test_envelope_reports_out_of_range_counts_and_requires_definition() -> None:
    series = sample_series()
    assert _envelope_outside(
        series, {"temperature_c": {"minimum": 170.0, "maximum": 175.0}}, 1000.0
    ) == {"temperature_c": 10}
    with pytest.raises(CalibrationSeriesError, match="no configured operating envelope"):
        _envelope_outside(series, {}, 1000.0)


def test_mass_flow_envelope_uses_the_model_density_not_an_assumption() -> None:
    series = sample_series()
    limits = {"feed_flow_kg_h": {"minimum": 35_000.0, "maximum": 37_000.0}}
    assert _envelope_outside(series, limits, density_kg_m3=500.0) == {}
    assert _envelope_outside(series, limits, density_kg_m3=1000.0) == {
        "feed_flow_kg_h": 10
    }
