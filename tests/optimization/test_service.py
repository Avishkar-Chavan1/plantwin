import pytest
from packages.optimization import OperatingEnvelope, OptimizationService
from packages.physics import CSTRInputs


def test_optimizer_respects_validated_bounds_and_is_reproducible() -> None:
    baseline = CSTRInputs(feed_temperature_k=453.15, pressure_pa=1_000_000, feed_flow_m3_s=0.020)
    service = OptimizationService()
    first = service.optimize(baseline)
    second = service.optimize(baseline)
    assert first.constraint_status == "PASS"
    assert service.envelope.contains(first.inputs)
    assert first.inputs.feed_temperature_k == pytest.approx(second.inputs.feed_temperature_k)


def test_invalid_bounds_and_unsafe_baseline_are_rejected() -> None:
    with pytest.raises(ValueError):
        OperatingEnvelope(temperature_k_min=500, temperature_k_max=400)
    with pytest.raises(ValueError):
        OptimizationService().optimize(CSTRInputs(feed_temperature_k=500.0))
