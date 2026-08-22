"""app.calibration 校准/温补层测试。

覆盖：未校准状态、校准过期、温度无效、温补模型不适时 κ(T)/κ25 的正确状态，
以及全链路的正常计算（用户示例：U=0.4V, I≈0.0005652A → κ(T)≈1413 μS/cm）。
"""

import pytest

from app import calibration, measurement


def _compute(**overrides) -> dict:
    defaults = dict(
        voltage_raw_v=0.4,
        current_raw_a=0.0005652,
        temperature_raw_c=25.0,
        cell_constant_cm_inv=1.0,
        alpha_per_c=0.02,
        compensation_model="linear",
    )
    defaults.update(overrides)
    return calibration.compute_iv(**defaults)


def test_full_chain_user_example():
    """用户示例：1413 µS/cm、Kcell=1 → U=0.4V、R≈707.7Ω、I≈0.0005652A。"""
    result = _compute()
    assert result["conductance_s"] == pytest.approx(0.001413, rel=1e-3)
    assert result["kappa_t_us_cm"] == pytest.approx(1413.0, rel=1e-3)
    assert result["kappa_25_us_cm"] == pytest.approx(1413.0, rel=1e-3)
    assert result["quality_flags"] == ()


def test_uncalibrated_no_fake_kappa():
    """无 Kcell：G 可算，κ(T) 为空并标记 UNCALIBRATED，κ25 必须为空。"""
    result = _compute(cell_constant_cm_inv=None)
    assert result["conductance_s"] is not None
    assert result["kappa_t_us_cm"] is None
    assert result["kappa_25_us_cm"] is None
    assert calibration.UNCALIBRATED in result["quality_flags"]


def test_negative_kcell_is_uncalibrated():
    result = _compute(cell_constant_cm_inv=-1.0)
    assert result["kappa_t_us_cm"] is None
    assert result["kappa_25_us_cm"] is None
    assert calibration.UNCALIBRATED in result["quality_flags"]


def test_calibration_expired_blocks_kappa25():
    """校准过期：κ(T) 保留，κ25 为空并标记 CALIBRATION_EXPIRED。"""
    result = _compute(calibration_expired=True)
    assert result["kappa_t_us_cm"] is not None
    assert result["kappa_25_us_cm"] is None
    assert calibration.CALIBRATION_EXPIRED in result["quality_flags"]


def test_invalid_temperature_blocks_kappa25():
    """温度无效（None / 越界 / 非有限）：不生成 κ25，标记 TEMPERATURE_INVALID。"""
    for temp in (None, 5.0, 50.0, float("nan")):
        result = _compute(temperature_raw_c=temp)
        assert result["kappa_t_us_cm"] is not None
        assert result["kappa_25_us_cm"] is None
        assert calibration.TEMPERATURE_INVALID in result["quality_flags"]


def test_unknown_compensation_model_blocks_kappa25():
    """温补模型不适用：κ(T) 有值，κ25 为空，标记 COMPENSATION_UNAVAILABLE。"""
    result = _compute(compensation_model="arrhenius")
    assert result["kappa_t_us_cm"] is not None
    assert result["kappa_25_us_cm"] is None
    assert calibration.COMPENSATION_UNAVAILABLE in result["quality_flags"]


def test_linear_without_alpha_blocks_kappa25():
    """linear 模型缺 α：κ25 为空，标记 COMPENSATION_UNAVAILABLE。"""
    result = _compute(alpha_per_c=None)
    assert result["kappa_t_us_cm"] is not None
    assert result["kappa_25_us_cm"] is None
    assert calibration.COMPENSATION_UNAVAILABLE in result["quality_flags"]


def test_none_model_skips_compensation():
    """compensation_model='none'：κ25 = κ(T)，不施加线性温补。"""
    result = _compute(compensation_model="none", temperature_raw_c=30.0)
    assert result["kappa_t_us_cm"] is not None
    assert result["kappa_25_us_cm"] == result["kappa_t_us_cm"]


def test_kappa_25_temperature_compensation():
    """κ25 = κ(T) / [1+α(T−25)]：T=35, α=0.02 → 分母 1.2。"""
    kt = measurement.kappa_t(0.001413, 1.0)
    k25 = calibration.kappa_25(kt, 35.0, 0.02, "linear")
    assert k25 == pytest.approx(1413.0 / 1.2)


def test_open_circuit_marks_open_circuit():
    """U=0：G/κ/κ25 全空，标记 OPEN_CIRCUIT。"""
    result = _compute(voltage_raw_v=0.0, current_raw_a=0.0)
    assert result["conductance_s"] is None
    assert result["kappa_t_us_cm"] is None
    assert result["kappa_25_us_cm"] is None
    assert calibration.OPEN_CIRCUIT in result["quality_flags"]


def test_extra_flags_preserved():
    """调用方已有的质量标志（如饱和/欠量程）被合并保留。"""
    result = _compute(extra_flags=(calibration.SATURATED, calibration.UNDER_RANGE))
    assert calibration.SATURATED in result["quality_flags"]
    assert calibration.UNDER_RANGE in result["quality_flags"]
