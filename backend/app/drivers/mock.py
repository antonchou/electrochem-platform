"""Deterministic, configurable U/I/T mock driver（电极 I–V 链路模拟）。

Mock 只模拟原始量：
1. 固定/可配置的激励电压 U（默认 0.4 V）；
2. 由目标 κ25 反推等效电阻 R = Kcell / (κ25·1e-6)，再求电流 I = U / R；
3. 模拟温度 T。
不在此处计算 G / κ(T) / κ25——交给软件计算层（app.measurement / app.calibration），
与未来硬件走同一条计算链，接入硬件时只替换数据源。
"""

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
    base_ec: float = 1413.0  # 目标 κ25（μS/cm）；旧字段名保留以兼容既有配置
    base_temperature: float = 25.0
    base_ph: float = 7.0
    ec_noise: float = 1.5
    temperature_noise: float = 0.15
    ph_noise: float = 0.01
    drift_ec_per_second: float = 0.2
    dropout_every_n: int = 10
    # ---- 电极 I–V 链路参数（仅用于反推等效阻抗/电流，计算链由上层完成） ----
    excitation_voltage_v: float = 0.4  # 激励电压有效值，V
    excitation_frequency_hz: float = 1000.0
    cell_constant_per_cm: float = 1.0  # Kcell，cm⁻¹（与校准层一致才能还原目标 κ25）
    alpha_per_c: float = 0.02  # 线性温补系数（仅配置透传，计算在 calibration 层）
    calibration_id: str = "CAL_MOCK_CONFIG"
    range_id: str = "R_100R_10K"
    sensor_path_id: str = "EC_IV_CELL_MOCK"

    def __post_init__(self) -> None:
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if not math.isfinite(self.base_ec) or self.base_ec < 0:
            raise ValueError("base_ec must be non-negative")
        if not math.isfinite(self.base_temperature):
            raise ValueError("base_temperature must be finite")
        if not 0 <= self.base_ph <= 14:
            raise ValueError("base_ph must be between 0 and 14")
        if not all(
            math.isfinite(value)
            for value in (self.ec_noise, self.temperature_noise, self.ph_noise)
        ) or min(self.ec_noise, self.temperature_noise, self.ph_noise) < 0:
            raise ValueError("noise values must be non-negative")
        if not math.isfinite(self.drift_ec_per_second):
            raise ValueError("drift_ec_per_second must be finite")
        if not isinstance(self.dropout_every_n, int) or self.dropout_every_n < 0:
            raise ValueError("dropout_every_n must be non-negative")
        if not math.isfinite(self.excitation_voltage_v) or self.excitation_voltage_v <= 0:
            raise ValueError("excitation_voltage_v must be positive")
        if not math.isfinite(self.excitation_frequency_hz) or self.excitation_frequency_hz <= 0:
            raise ValueError("excitation_frequency_hz must be positive")
        if not math.isfinite(self.cell_constant_per_cm) or self.cell_constant_per_cm <= 0:
            raise ValueError("cell_constant_per_cm must be positive")
        if not math.isfinite(self.alpha_per_c) or not 0 <= self.alpha_per_c < 1.0 / 15.0:
            raise ValueError("alpha_per_c must be in [0, 1/15)")
        if not isinstance(self.calibration_id, str) or not self.calibration_id.strip():
            raise ValueError("calibration_id must be non-empty")
        if not isinstance(self.range_id, str) or not self.range_id.strip():
            raise ValueError("range_id must be non-empty")
        if not isinstance(self.sensor_path_id, str) or not self.sensor_path_id.strip():
            raise ValueError("sensor_path_id must be non-empty")

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
            "excitation_voltage_v",
            "excitation_frequency_hz",
            "cell_constant_per_cm",
            "alpha_per_c",
            "calibration_id",
            "range_id",
            "sensor_path_id",
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
    """Generate repeatable raw U/I/T fixture data; values are not hardware claims."""

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
            return DriverReading(quality_flags=("SIMULATED", "DROPOUT"))

        noise_scale = 8.0 if self.config.scenario is MockScenario.NOISY else 1.0
        target_k25 = (
            self.config.base_ec
            + math.sin(elapsed_seconds / 30.0) * 6.0
            + self._random.uniform(-self.config.ec_noise, self.config.ec_noise)
            * noise_scale
        )
        if self.config.scenario is MockScenario.DRIFT:
            target_k25 += self.config.drift_ec_per_second * elapsed_seconds

        temperature = round(
            self.config.base_temperature
            + self._random.uniform(
                -self.config.temperature_noise,
                self.config.temperature_noise,
            )
            * noise_scale,
            2,
        )
        ph = (
            self.config.base_ph
            + 0.04 * math.sin(elapsed_seconds / 25.0)
            + self._random.uniform(-self.config.ph_noise, self.config.ph_noise)
            * noise_scale
        )

        flags = ["SIMULATED"]
        if not 0 <= ph <= 14:
            flags.append("OUT_OF_RANGE")
        if not 10.0 <= temperature <= 40.0:
            flags.append("TEMPERATURE_INVALID")

        # 由目标 κ25 先还原实测温度下的 κ(T)，再反推等效阻抗与电流：
        #   κ(T)=κ25·[1+α(T−25)]，G=κ(T)/Kcell，R=1/G，I=U/R。
        voltage = round(self.config.excitation_voltage_v, 4)
        denominator = 1.0 + self.config.alpha_per_c * (temperature - 25.0)
        target_kappa_t = target_k25 * denominator
        if target_k25 <= 0 or target_kappa_t <= 0 or not math.isfinite(target_kappa_t):
            flags.append("OUT_OF_RANGE")
            return DriverReading(
                voltage_raw_v=voltage,
                current_raw_a=None,
                temperature_raw_c=temperature,
                quality_flags=tuple(dict.fromkeys(flags)),
            )
        resistance = self.config.cell_constant_per_cm / (target_kappa_t * 1e-6)
        current = round(voltage / resistance, 9)

        return DriverReading(
            voltage_raw_v=voltage,
            current_raw_a=current,
            temperature_raw_c=temperature,
            quality_flags=tuple(flags),
        )
