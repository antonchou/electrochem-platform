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
