import pytest
from packages.units import convert, to_si


def test_temperature_conversion_uses_kelvin_internally() -> None:
    assert to_si(180.0, "degC") == pytest.approx(453.15)
    assert convert(453.15, "K", "degC") == pytest.approx(180.0)


def test_cross_dimension_conversion_is_rejected() -> None:
    with pytest.raises(ValueError):
        convert(1.0, "bar", "K")
