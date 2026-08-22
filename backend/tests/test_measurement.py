"""app.measurement 纯物理计算层测试（G=I/U、κ(T)=Kcell·G·1e6）。"""

import math

import pytest

from app import measurement


def test_conductance_basic():
    """G = I / U，单位 S。"""
    assert measurement.conductance(1.0, 1.0) == pytest.approx(1.0)
    # 用户示例：U=0.4V, I=0.0005652A → G≈0.001413 S
    assert measurement.conductance(0.0005652, 0.4) == pytest.approx(0.001413, rel=1e-3)


def test_conductance_zero_voltage_no_division_by_zero():
    """U=0（开路/无激励）不得除零，返回 None。"""
    assert measurement.conductance(0.001, 0.0) is None


def test_conductance_missing_or_invalid_inputs():
    assert measurement.conductance(None, 1.0) is None
    assert measurement.conductance(1.0, None) is None
    assert measurement.conductance(math.nan, 1.0) is None
    assert measurement.conductance(1.0, math.inf) is None


def test_kappa_t_unit_conversion():
    """κ(T) = Kcell · G · 1e6：1 S·cm⁻¹ → 1e6 μS/cm；G=0.001413→1413 μS/cm。"""
    assert measurement.kappa_t(0.001413, 1.0) == pytest.approx(1413.0)
    assert measurement.kappa_t(1.0, 1.0) == pytest.approx(1_000_000.0)
    assert measurement.kappa_t(0.001413, 0.5) == pytest.approx(706.5)


def test_kappa_t_invalid_inputs():
    assert measurement.kappa_t(None, 1.0) is None
    assert measurement.kappa_t(1.0, None) is None
    assert measurement.kappa_t(1.0, -1.0) is None  # Kcell 不可为负
    assert measurement.kappa_t(math.inf, 1.0) is None
