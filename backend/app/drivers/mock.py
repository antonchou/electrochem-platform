"""Deterministic, configurable EC/temperature/pH mock driver."""

from __future__ import annotations

import json
import math
import os
import random
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .base import DeviceDriver, DriverReading


class MockScenario(str, Enum):
    STABLE = "stable"
    NOISY = "noisy"
    DRIFT = "drift"
    DROPOUT = "dropout"


@dataclass(frozen=True, slots=True)
class MockDeviceConfig:
    scenario: MockScenario = MockScenario.STABLE
    seed: int = 2026
    sample_rate_hz: float = 10.0
    base_ec: float = 1413.0
    base_temperature: float = 25.0
    base_ph: float = 7.0
    ec_noise: float = 1.5
    temperature_noise: float = 0.15
    ph_noise: float = 0.01
    drift_ec_per_second: float = 0.2
    dropout_every_n: int = 10

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.base_ec < 0:
            raise ValueError("base_ec must be non-negative")
        if not 0 <= self.base_ph <= 14:
            raise ValueError("base_ph must be between 0 and 14")
        if min(self.ec_noise, self.temperature_noise, self.ph_noise) < 0:
            raise ValueError("noise values must be non-negative")
        if self.dropout_every_n < 0:
            raise ValueError("dropout_every_n must be non-negative")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "MockDeviceConfig":
        allowed = {
            "scenario",
            "seed",
            "sample_rate_hz",
            "base_ec",
            "base_temperature",
            "base_ph",
            "ec_noise",
            "temperature_noise",
            "ph_noise",
            "drift_ec_per_second",
            "dropout_every_n",
        }
        unknown = set(raw) - allowed - {"schema_version", "driver"}
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"unknown mock config fields: {names}")
        values = {key: value for key, value in raw.items() if key in allowed}
        if "scenario" in values:
            values["scenario"] = MockScenario(values["scenario"])
        return cls(**values)

    @classmethod
    def from_json_file(cls, path: str | Path) -> "MockDeviceConfig":
        config_path = Path(path)
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("mock config root must be an object")
        return cls.from_mapping(raw)


def load_mock_config() -> MockDeviceConfig:
    """Load the optional versioned config and apply small env overrides."""
    config_path = os.environ.get("EC_MOCK_CONFIG")
    config = (
        MockDeviceConfig.from_json_file(config_path)
        if config_path
        else MockDeviceConfig()
    )
    overrides: dict[str, Any] = {
        "scenario": os.environ.get("EC_MOCK_SCENARIO"),
        "sample_rate_hz": os.environ.get("EC_SAMPLE_RATE_HZ"),
        "seed": os.environ.get("EC_MOCK_SEED"),
    }
    raw = {
        field: getattr(config, field)
        for field in MockDeviceConfig.__dataclass_fields__
    }
    if overrides["scenario"] is not None:
        raw["scenario"] = MockScenario(overrides["scenario"])
    if overrides["sample_rate_hz"] is not None:
        raw["sample_rate_hz"] = float(overrides["sample_rate_hz"])
    if overrides["seed"] is not None:
        raw["seed"] = int(overrides["seed"])
    return MockDeviceConfig(**raw)


class MockDevice(DeviceDriver):
    """Generate repeatable fixture data; values are not hardware claims."""

    def __init__(self, config: MockDeviceConfig | None = None) -> None:
        self.config = config or MockDeviceConfig()
        self._random = random.Random(self.config.seed)
        self._connected = False
        self._read_count = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def read_count(self) -> int:
        return self._read_count

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def read(self, elapsed_seconds: float) -> DriverReading:
        if not self._connected:
            raise RuntimeError("mock device is not connected")

        index = self._read_count
        self._read_count += 1
        if (
            self.config.scenario is MockScenario.DROPOUT
            and self.config.dropout_every_n > 0
            and (index + 1) % self.config.dropout_every_n == 0
        ):
            return DriverReading(
                ec=None,
                temperature=None,
                ph=None,
                quality_flags=("SIMULATED", "DROPOUT"),
            )

        noise_scale = 8.0 if self.config.scenario is MockScenario.NOISY else 1.0
        ec = (
            self.config.base_ec
            + math.sin(elapsed_seconds / 30.0) * 6.0
            + self._random.uniform(-self.config.ec_noise, self.config.ec_noise)
            * noise_scale
        )
        if self.config.scenario is MockScenario.DRIFT:
            ec += self.config.drift_ec_per_second * elapsed_seconds

        temperature = (
            self.config.base_temperature
            + self._random.uniform(
                -self.config.temperature_noise,
                self.config.temperature_noise,
            )
            * noise_scale
        )
        ph = (
            self.config.base_ph
            + 0.04 * math.sin(elapsed_seconds / 25.0)
            + self._random.uniform(-self.config.ph_noise, self.config.ph_noise)
            * noise_scale
        )

        flags = ["SIMULATED"]
        if ec < 0 or not 0 <= ph <= 14:
            flags.append("OUT_OF_RANGE")
        return DriverReading(
            ec=round(ec, 1),
            temperature=round(temperature, 2),
            ph=round(ph, 3),
            quality_flags=tuple(flags),
        )
