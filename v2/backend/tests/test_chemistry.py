import math

import pytest

from ec.chemistry import STANDARDS, compensation_factor, derive, kcl_kappa25_us_cm


def test_chain_from_raw_values():
    # U = 0.1 V、I = 1e-4 A → G = 1e-3 S；Kcell = 1.2 → κ(T) = 1200 µS/cm；30 °C、α = 0.02 → ÷1.1
    d = derive(0.1, 1e-4, 30.0, 1.2, 0.02)
    assert d.conductance_s == pytest.approx(1e-3)
    assert d.kappa_t_us_cm == pytest.approx(1200.0)
    assert d.kappa25_us_cm == pytest.approx(1200.0 / 1.1)
    assert not d.polarity_error


def test_missing_temperature_keeps_conductance_but_no_kappa25():
    d = derive(0.1, 1e-4, None, 1.0, 0.02)
    assert d.kappa_t_us_cm == pytest.approx(1000.0)
    assert d.kappa25_us_cm is None


@pytest.mark.parametrize(("u", "i"), [(0.0, 1e-4), (-0.1, 1e-4), (0.1, -1e-4)])
def test_reversed_wiring_is_a_polarity_error(u, i):
    d = derive(u, i, 25.0, 1.0, 0.02)
    assert d.polarity_error
    assert d.conductance_s is None and d.kappa25_us_cm is None


@pytest.mark.parametrize(("u", "i"), [(None, 1e-4), (0.1, None), (math.nan, 1e-4), (0.1, math.inf)])
def test_missing_or_nonfinite_raw_values_give_nothing(u, i):
    d = derive(u, i, 25.0, 1.0, 0.02)
    assert d.conductance_s is None and not d.polarity_error


def test_zero_current_is_zero_conductivity():
    assert derive(0.2, 0.0, 25.0, 1.0, 0.02).kappa25_us_cm == 0.0


def test_overflow_from_tiny_voltage_gives_nothing():
    assert derive(1e-320, 1.0, 25.0, 1.0, 0.02).conductance_s is None


def test_compensation_factor_outside_model_range_is_none():
    assert compensation_factor(25.0, 0.02) == 1.0
    assert compensation_factor(-30.0, 0.02) is None  # 1 + 0.02·(−55) < 0
    assert compensation_factor(None, 0.02) is None


def test_kcl_model_reproduces_standard_solutions():
    by_name = {s.name: s.kappa25_us_cm for s in STANDARDS}
    assert kcl_kappa25_us_cm(1.0) == pytest.approx(by_name["KCl 0.001 mol/L"], rel=0.005)
    assert kcl_kappa25_us_cm(10.0) == pytest.approx(by_name["KCl 0.01 mol/L"], rel=0.005)
    assert kcl_kappa25_us_cm(100.0) == pytest.approx(by_name["KCl 0.1 mol/L"], rel=0.005)
    assert kcl_kappa25_us_cm(0.0) == 0.0
    assert kcl_kappa25_us_cm(10_000.0) > 0  # 远超表格也不会变负
