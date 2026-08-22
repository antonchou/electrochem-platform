"""Driver contract and deterministic Mock scenarios."""

import asyncio

import pytest

from app.drivers import MockDevice, MockDeviceConfig, MockScenario, load_mock_config


def test_mock_device_is_seeded_and_repeatable():
    async def scenario() -> None:
        config = MockDeviceConfig(seed=7, scenario=MockScenario.STABLE)
        left = MockDevice(config)
        right = MockDevice(config)
        await left.connect()
        await right.connect()

        times = [0.0, 0.1, 0.2, 0.3]
        left_values = [await left.read(t) for t in times]
        right_values = [await right.read(t) for t in times]

        assert left_values == right_values
        assert all(item.complete_for_conductivity for item in left_values)
        assert all("SIMULATED" in item.quality_flags for item in left_values)

        await left.close()
        await right.close()

    asyncio.run(scenario())


def test_mock_device_emits_raw_u_i_t():
    """Mock 只产原始 U/I/T；由测量/校准层可还原目标 κ25≈1413 μS/cm。"""
    from app import calibration, measurement

    async def scenario() -> None:
        device = MockDevice(MockDeviceConfig(seed=7, scenario=MockScenario.STABLE))
        await device.connect()
        reading = await device.read(0.0)

        assert reading.complete_for_iv is True
        assert reading.voltage_raw_v is not None
        assert reading.current_raw_a is not None
        assert reading.temperature_raw_c is not None
        # 驱动层不附带派生量（计算链在 app.measurement / app.calibration）
        assert not hasattr(reading, "conductance_s")
        assert not hasattr(reading, "kappa_t_us_cm")
        assert not hasattr(reading, "kappa_25_us_cm")

        # 还原：G=I/U → κ(T)=Kcell·G → κ25（默认 Kcell=1, α=0.02）
        g = measurement.conductance(reading.current_raw_a, reading.voltage_raw_v)
        assert g is not None
        computed = calibration.compute_iv(
            voltage_raw_v=reading.voltage_raw_v,
            current_raw_a=reading.current_raw_a,
            temperature_raw_c=reading.temperature_raw_c,
            cell_constant_cm_inv=1.0,
            alpha_per_c=0.02,
            compensation_model="linear",
        )
        assert computed["conductance_s"] == pytest.approx(g)
        # 基值 1413 μS/cm 附近（含噪声）
        assert computed["kappa_25_us_cm"] is not None
        assert 1390 <= computed["kappa_25_us_cm"] <= 1440
        # 与用户示例一致的数量级：U≈0.4V，I≈0.00057A
        assert reading.voltage_raw_v == pytest.approx(0.4, abs=1e-4)
        assert 0.0005 <= reading.current_raw_a <= 0.0006

        await device.close()

    asyncio.run(scenario())


def test_mock_reconstructs_target_k25_away_from_25c():
    """反推原始电流时必须先把目标 κ25 换算为 κ(T)。"""
    from app import calibration

    async def scenario() -> None:
        config = MockDeviceConfig(
            base_ec=1413.0,
            base_temperature=35.0,
            ec_noise=0.0,
            temperature_noise=0.0,
        )
        device = MockDevice(config)
        await device.connect()
        reading = await device.read(0.0)
        result = calibration.compute_iv(
            voltage_raw_v=reading.voltage_raw_v,
            current_raw_a=reading.current_raw_a,
            temperature_raw_c=reading.temperature_raw_c,
            cell_constant_cm_inv=config.cell_constant_per_cm,
            alpha_per_c=config.alpha_per_c,
        )
        assert result["kappa_25_us_cm"] == pytest.approx(1413.0, abs=0.01)

    asyncio.run(scenario())


def test_mock_zero_target_is_quality_reading_not_division_error():
    async def scenario() -> None:
        device = MockDevice(MockDeviceConfig(base_ec=0.0, ec_noise=0.0))
        await device.connect()
        reading = await device.read(0.0)
        assert reading.complete_for_iv is False
        assert reading.current_raw_a is None
        assert "OUT_OF_RANGE" in reading.quality_flags

    asyncio.run(scenario())


def test_mock_rejects_zero_cell_constant():
    with pytest.raises(ValueError, match="cell_constant_per_cm must be positive"):
        MockDeviceConfig(cell_constant_per_cm=0.0)


def test_mock_dropout_has_no_iv_data():
    async def scenario() -> None:
        device = MockDevice(
            MockDeviceConfig(
                scenario=MockScenario.DROPOUT,
                dropout_every_n=2,
                seed=1,
            )
        )
        await device.connect()
        first = await device.read(0.0)
        dropped = await device.read(0.1)
        assert first.complete_for_iv is True
        assert dropped.complete_for_iv is False
        assert dropped.quality_flags == ("SIMULATED", "DROPOUT")

    asyncio.run(scenario())


def test_dropout_scenario_marks_missing_reading():
    async def scenario() -> None:
        device = MockDevice(
            MockDeviceConfig(
                scenario=MockScenario.DROPOUT,
                dropout_every_n=3,
                seed=1,
            )
        )
        await device.connect()
        readings = [await device.read(index / 10) for index in range(3)]

        assert readings[0].complete_for_conductivity is True
        assert readings[2].complete_for_conductivity is False
        assert readings[2].quality_flags == ("SIMULATED", "DROPOUT")

    asyncio.run(scenario())


def test_mock_device_requires_connection():
    async def scenario() -> None:
        device = MockDevice()
        with pytest.raises(RuntimeError, match="not connected"):
            await device.read(0.0)

    asyncio.run(scenario())


def test_mock_config_rejects_unknown_fields():
    with pytest.raises(ValueError, match="unknown mock config fields"):
        MockDeviceConfig.from_mapping({"scenario": "stable", "typo": 1})


def test_mock_env_error_names_the_invalid_variable(monkeypatch):
    monkeypatch.setenv("EC_SAMPLE_RATE_HZ", "fast")
    with pytest.raises(ValueError, match="EC_SAMPLE_RATE_HZ"):
        load_mock_config()
