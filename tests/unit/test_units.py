import pytest
from packages.units import convert, to_si


def test_temperature_conversion_uses_kelvin_internally() -> None:
    assert to_si(180.0, "degC") == pytest.approx(453.15)
    assert convert(453.15, "K", "degC") == pytest.approx(180.0)


def test_industrial_units_normalize_to_si() -> None:
    assert to_si(25.0, "degC") == pytest.approx(298.15)
    assert to_si(1.5, "kPa") == pytest.approx(1500.0)
    assert to_si(90.0, "L/min") == pytest.approx(0.0015)
    assert to_si(7.2, "kg/h") == pytest.approx(0.002)
    assert to_si(15.0, "kW") == pytest.approx(15000.0)
    assert to_si(3.5, "kJ/kg-K") == pytest.approx(3500.0)
    assert convert(3500.0, "J/kg-K", "kJ/kg-K") == pytest.approx(3.5)
    assert to_si(100.0, "%") == pytest.approx(1.0)
    assert to_si(60.0, "rpm") == pytest.approx(2 * 3.141592653589793)


def test_cross_dimension_conversion_is_rejected() -> None:
    with pytest.raises(ValueError):
        convert(1.0, "bar", "K")
