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
    # I–V 链路参数（REQ-M-001）：激励电压、电池常数、温补系数
    cell_constant_per_cm: float = 1.0
    alpha_per_c: float = 0.02
    excitation_voltage_v: float = 1.0
    excitation_frequency_hz: float = 0.0  # 0 = 仿真直流 I–V；真实硬件填交流频率
    compensation_model: str = "linear_alpha"
    device_id: str = "MOCK-IV-01"
    firmware_version: str = "0.1.0"
    range_id: str = "WIDE"

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
        if self.cell_constant_per_cm <= 0:
            raise ValueError("cell_constant_per_cm must be positive")
        if self.excitation_voltage_v <= 0:
            raise ValueError("excitation_voltage_v must be positive")

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
            "cell_constant_per_cm",
            "alpha_per_c",
            "excitation_voltage_v",
            "excitation_frequency_hz",
            "compensation_model",
            "device_id",
            "firmware_version",
            "range_id",
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
    """Load config with actionable errors for invalid files/environment values."""
    config_path = os.environ.get("EC_MOCK_CONFIG")
    try:
        config = (
            MockDeviceConfig.from_json_file(config_path)
            if config_path
            else MockDeviceConfig()
        )
    except (OSError, TypeError, ValueError) as exc:
        location = config_path or "built-in defaults"
        raise ValueError(f"invalid mock configuration ({location}): {exc}") from exc
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
        try:
            raw["scenario"] = MockScenario(overrides["scenario"])
        except ValueError as exc:
            choices = ", ".join(item.value for item in MockScenario)
            raise ValueError(
                f"invalid EC_MOCK_SCENARIO={overrides['scenario']!r}; expected one of: {choices}"
            ) from exc
    if overrides["sample_rate_hz"] is not None:
        try:
            raw["sample_rate_hz"] = float(overrides["sample_rate_hz"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid EC_SAMPLE_RATE_HZ={overrides['sample_rate_hz']!r}; expected a positive number"
            ) from exc
    if overrides["seed"] is not None:
        try:
            raw["seed"] = int(overrides["seed"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid EC_MOCK_SEED={overrides['seed']!r}; expected an integer"
            ) from exc
    try:
        return MockDeviceConfig(**raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid mock configuration values: {exc}") from exc


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
        # 目标 κ25（真实值，μS/cm）：模拟“溶液中真实电导率”随漂移/噪声变化
        kappa25 = (
            self.config.base_ec
            + math.sin(elapsed_seconds / 30.0) * 6.0
            + self._random.uniform(-self.config.ec_noise, self.config.ec_noise)
            * noise_scale
        )
        if self.config.scenario is MockScenario.DRIFT:
            kappa25 += self.config.drift_ec_per_second * elapsed_seconds

        temperature = (
            self.config.base_temperature
            + self._random.uniform(
                -self.config.temperature_noise,
                self.config.temperature_noise,
            )
            * noise_scale
        )
        # I–V 仿真（REQ-M-001 软件侧）：受控激励 U，由 κ(T) 反推回路电流 I，
        # 使软件计算链 G=I/U → κ(T)=Kcell·G → κ25 能还原目标 κ25。
        #   κ(T) = κ25·(1+α·(T-25))；G = κ(T)·1e-6 / Kcell [S]；I = G·U [A]
        u_nominal = self.config.excitation_voltage_v
        kappa_t = kappa25 * (1.0 + self.config.alpha_per_c * (temperature - 25.0))
        g_s = kappa_t * 1e-6 / self.config.cell_constant_per_cm
        i_nominal = g_s * u_nominal
        # 激励/电流的真实波动（小噪声，模拟前端噪声）
        u = u_nominal * (1.0 + self._random.uniform(-0.001, 0.001))
        i = i_nominal * (1.0 + self._random.uniform(-0.005, 0.005))

        ph = (
            self.config.base_ph
            + 0.04 * math.sin(elapsed_seconds / 25.0)
            + self._random.uniform(-self.config.ph_noise, self.config.ph_noise)
            * noise_scale
        )

        flags = ["SIMULATED"]
        if kappa25 < 0 or not 0 <= ph <= 14:
            flags.append("OUT_OF_RANGE")
        return DriverReading(
            ec=round(kappa25, 1),
            temperature=round(temperature, 2),
            ph=round(ph, 3),
            voltage_v=round(u, 6),
            current_a=round(i, 9),
            quality_flags=tuple(flags),
        )
