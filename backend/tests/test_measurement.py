"""I–V 计算链单测（REQ-M-001 软件侧）：G=I/U → κ(T)=Kcell·G → κ25 温补。

对应新路线图 N4 验收标准①：数值单测误差 <0.1%。
"""

import math

import pytest

from app.measurement import compute_chain

KCELL = 1.0
ALPHA = 0.02


def test_basic_chain_roundtrip():
    """给定 U=1.0V, I=1.413mA, T=25°C, Kcell=1.0 → κ25 = 1413 μS/cm。"""
    result = compute_chain(1.0, 1.413e-3, 25.0, KCELL, ALPHA)
    assert result.conductance_s == pytest.approx(1.413e-3, rel=1e-9)
    assert result.kappa_t_us_cm == pytest.approx(1413.0, rel=1e-9)
    assert result.kappa_25_us_cm == pytest.approx(1413.0, rel=1e-9)


def test_temperature_compensation_raises_kappa25():
    """T>25°C 时 κ(T) 更高，温补后 κ25 应回到 25°C 水平。"""
    # T=30°C, κ25 目标 1000 → κ(T) = 1000*(1+0.02*5) = 1100
    # G = κ(T)*1e-6/Kcell = 1.1e-3, I = G*U = 1.1e-3 A @ U=1
    result = compute_chain(1.0, 1.1e-3, 30.0, KCELL, ALPHA)
    assert result.kappa_t_us_cm == pytest.approx(1100.0, rel=1e-9)
    assert result.kappa_25_us_cm == pytest.approx(1000.0, rel=1e-9)


def test_lower_temperature_decreases_kappa():
    """T<25°C 时 κ(T) 更低，温补后 κ25 仍回到 25°C 水平。"""
    # T=20°C, κ25 目标 1000 → κ(T) = 1000*(1-0.02*5) = 900
    result = compute_chain(1.0, 0.9e-3, 20.0, KCELL, ALPHA)
    assert result.kappa_t_us_cm == pytest.approx(900.0, rel=1e-9)
    assert result.kappa_25_us_cm == pytest.approx(1000.0, rel=1e-9)


def test_cell_constant_scales_kappa():
    """Kcell=2.0 时同 I/U 的 κ 是 Kcell=1.0 的两倍。"""
    r1 = compute_chain(1.0, 1.0e-3, 25.0, 1.0, ALPHA)
    r2 = compute_chain(1.0, 1.0e-3, 25.0, 2.0, ALPHA)
    assert r2.kappa_t_us_cm == pytest.approx(2 * r1.kappa_t_us_cm, rel=1e-9)
    assert r2.kappa_25_us_cm == pytest.approx(2 * r1.kappa_25_us_cm, rel=1e-9)


def test_nonfinite_inputs_rejected():
    with pytest.raises(ValueError, match="finite"):
        compute_chain(float("nan"), 1e-3, 25.0, KCELL, ALPHA)
    with pytest.raises(ValueError, match="finite"):
        compute_chain(float("inf"), 1e-3, 25.0, KCELL, ALPHA)
    with pytest.raises(ValueError, match="finite"):
        compute_chain(1.0, float("nan"), 25.0, KCELL, ALPHA)


def test_invalid_voltage_rejected():
    with pytest.raises(ValueError, match="voltage_v"):
        compute_chain(0.0, 1e-3, 25.0, KCELL, ALPHA)
    with pytest.raises(ValueError, match="voltage_v"):
        compute_chain(-1.0, 1e-3, 25.0, KCELL, ALPHA)


def test_invalid_cell_constant_rejected():
    with pytest.raises(ValueError, match="cell_constant"):
        compute_chain(1.0, 1e-3, 25.0, 0.0, ALPHA)


def test_temperature_denominator_zero_rejected():
    """α=0.04, T=0°C → 1+0.04*(0-25)=0 → 无法温补。"""
    with pytest.raises(ValueError, match="denominator"):
        compute_chain(1.0, 1e-3, 0.0, KCELL, 0.04)


def test_mock_iv_backderivation_consistent():
    """Mock 由目标 κ25 反推的 I，经计算链应还原出目标 κ25（N4 验收①）。"""
    from app.drivers import MockDevice, MockDeviceConfig, MockScenario

    async def scenario():
        cfg = MockDeviceConfig(
            seed=42,
            base_ec=1413.0,
            base_temperature=25.0,
            cell_constant_per_cm=1.0,
            alpha_per_c=0.02,
            excitation_voltage_v=1.0,
            scenario=MockScenario.STABLE,
        )
        device = MockDevice(cfg)
        await device.connect()
        reading = await device.read(0.0)
        assert reading.complete_for_iv
        result = compute_chain(
            reading.voltage_v,
            reading.current_a,
            reading.temperature,
            cfg.cell_constant_per_cm,
            cfg.alpha_per_c,
        )
        # 还原的 κ25 应接近目标 base_ec（噪声 <1.5 μS/cm + 电流噪声 0.5%）
        assert math.isclose(result.kappa_25_us_cm, cfg.base_ec, abs_tol=3.0)
        await device.close()

    import asyncio

    asyncio.run(scenario())
