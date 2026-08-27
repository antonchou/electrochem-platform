"""Hardware-stand-in experiment simulator.

Outputs the same DriverReading (raw V/I/T) as a future ADS1256 adapter.
The measurement layer still computes G/κ; this module never emits derived
conductivity as if it were a hardware quantity.

All readings carry SIMULATED. Default process still uses MockDevice;
enable with EC_DRIVER=simulator so production mock/kiosk behavior is unchanged.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .base import DeviceDriver, DriverReading


class SimulatorMode(str, Enum):
    STABLE = "stable"
    REALISTIC = "realistic"
    FAULT = "fault"


class FaultKind(str, Enum):
    NONE = "none"
    DISCONNECTED = "disconnected"
    CURRENT_ZERO = "current_zero"
    VOLTAGE_OOR = "voltage_oor"
    ADC_SAT = "adc_sat"
    TEMP_ABNORMAL = "temp_abnormal"
    DROPOUT = "dropout"
    UNSTABLE = "unstable"


# Mode presets fill noise/offset. Explicit config fields override these.
_MODE_PRESETS: dict[SimulatorMode, dict[str, Any]] = {
    SimulatorMode.STABLE: {
        "voltage_noise_v": 0.0003,
        "current_noise_a": 1.0e-6,
        "voltage_offset_v": 0.0,
        "current_offset_a": 0.0,
        "temperature_noise": 0.05,
        "drift_g_per_second": 0.0,
        "nonlinearity": 0.0,
        "polarization_tau_s": 0.0,
        "fault_kind": FaultKind.NONE,
        "dropout_every_n": 0,
    },
    SimulatorMode.REALISTIC: {
        "voltage_noise_v": 0.002,
        "current_noise_a": 8.0e-6,
        "voltage_offset_v": 0.002,
        "current_offset_a": 2.0e-6,
        "temperature_noise": 0.25,
        "drift_g_per_second": 1.0e-7,
        "nonlinearity": 5.0e-5,
        "polarization_tau_s": 0.15,
        "fault_kind": FaultKind.NONE,
        "dropout_every_n": 0,
    },
    SimulatorMode.FAULT: {
        "voltage_noise_v": 0.003,
        "current_noise_a": 1.2e-5,
        "voltage_offset_v": 0.003,
        "current_offset_a": 3.0e-6,
        "temperature_noise": 0.4,
        "drift_g_per_second": 2.0e-7,
        "nonlinearity": 8.0e-5,
        "polarization_tau_s": 0.2,
        "fault_kind": FaultKind.DROPOUT,
        "dropout_every_n": 8,
    },
}


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    """Virtual cell + excitation sweep. Field names overlap MockDeviceConfig where they mean the same thing."""

    mode: SimulatorMode = SimulatorMode.STABLE
    seed: int = 2026
    sample_rate_hz: float = 10.0
    # Virtual solution (demo/test only — not a lab measurement)
    solution_id: str = "DEMO_KCL_1413_SIMULATED"
    nominal_conductance_s: float = 1.413e-3  # G@25°C
    base_temperature: float = 25.0
    alpha_per_c: float = 0.02
    cell_constant_per_cm: float = 1.0
    # Excitation sweep (commanded DAC voltage). Must stay >0 so compute_chain accepts it.
    sweep_start_v: float = 0.2
    sweep_end_v: float = 1.0
    settle_seconds: float = 0.5
    sweep_seconds: float = 8.0
    sweep_repeat: bool = False
    excitation_frequency_hz: float = 1000.0
    excitation_amplitude_v: float = 1.0
    compensation_model: str = "linear_alpha"
    device_id: str = "SIM-IV-01"
    firmware_version: str = "0.1.0"
    range_id: str = "SIM"
    # Effects (mode presets if omitted)
    voltage_noise_v: float = 0.0003
    current_noise_a: float = 1.0e-6
    voltage_offset_v: float = 0.0
    current_offset_a: float = 0.0
    temperature_noise: float = 0.05
    drift_g_per_second: float = 0.0
    nonlinearity: float = 0.0
    polarization_tau_s: float = 0.0
    fault_kind: FaultKind = FaultKind.NONE
    dropout_every_n: int = 0
    fault_start_s: float = 0.3

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.nominal_conductance_s < 0:
            raise ValueError("nominal_conductance_s must be non-negative")
        if self.cell_constant_per_cm <= 0:
            raise ValueError("cell_constant_per_cm must be positive")
        if self.sweep_start_v <= 0 or self.sweep_end_v <= 0:
            raise ValueError("sweep voltages must be positive (compute_chain requires U>0)")
        if self.sweep_seconds < 0 or self.settle_seconds < 0:
            raise ValueError("settle_seconds and sweep_seconds must be non-negative")
        if min(
            self.voltage_noise_v,
            self.current_noise_a,
            self.temperature_noise,
            self.polarization_tau_s,
        ) < 0:
            raise ValueError("noise and tau values must be non-negative")
        if self.dropout_every_n < 0:
            raise ValueError("dropout_every_n must be non-negative")

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "SimulatorConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = set(raw) - allowed - {"schema_version", "driver", "simulated"}
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"unknown simulator config fields: {names}")
        mode_raw = raw.get("mode", SimulatorMode.STABLE.value)
        try:
            mode = SimulatorMode(mode_raw)
        except ValueError as exc:
            choices = ", ".join(item.value for item in SimulatorMode)
            raise ValueError(f"invalid mode={mode_raw!r}; expected one of: {choices}") from exc
        values: dict[str, Any] = dict(_MODE_PRESETS[mode])
        values["mode"] = mode
        for key, value in raw.items():
            if key in allowed:
                values[key] = value
        if isinstance(values.get("fault_kind"), str):
            values["fault_kind"] = FaultKind(values["fault_kind"])
        if isinstance(values.get("mode"), str):
            values["mode"] = SimulatorMode(values["mode"])
        return cls(**values)

    @classmethod
    def from_json_file(cls, path: str | Path) -> "SimulatorConfig":
        config_path = Path(path)
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("simulator config root must be an object")
        return cls.from_mapping(raw)


def load_simulator_config() -> SimulatorConfig:
    config_path = os.environ.get("EC_SIM_CONFIG")
    try:
        config = (
            SimulatorConfig.from_json_file(config_path) if config_path else SimulatorConfig()
        )
    except (OSError, TypeError, ValueError) as exc:
        location = config_path or "built-in defaults"
        raise ValueError(f"invalid simulator configuration ({location}): {exc}") from exc
    raw = {field: getattr(config, field) for field in SimulatorConfig.__dataclass_fields__}
    mode_override = os.environ.get("EC_SIM_MODE")
    if mode_override is not None:
        try:
            mode = SimulatorMode(mode_override)
        except ValueError as exc:
            choices = ", ".join(item.value for item in SimulatorMode)
            raise ValueError(
                f"invalid EC_SIM_MODE={mode_override!r}; expected one of: {choices}"
            ) from exc
        raw.update(_MODE_PRESETS[mode])
        raw["mode"] = mode
    rate = os.environ.get("EC_SAMPLE_RATE_HZ")
    if rate is not None:
        try:
            raw["sample_rate_hz"] = float(rate)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid EC_SAMPLE_RATE_HZ={rate!r}") from exc
    seed = os.environ.get("EC_SIM_SEED") or os.environ.get("EC_MOCK_SEED")
    if seed is not None:
        try:
            raw["seed"] = int(seed)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid simulator seed={seed!r}") from exc
    g = os.environ.get("EC_SIM_G")
    if g is not None:
        try:
            raw["nominal_conductance_s"] = float(g)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid EC_SIM_G={g!r}") from exc
    try:
        return SimulatorConfig(**raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid simulator configuration values: {exc}") from exc


class SimulatorDriver(DeviceDriver):
    """Simulate a two-electrode cell under a voltage sweep. Not a hardware claim."""

    def __init__(self, config: SimulatorConfig | None = None) -> None:
        self.config = config or SimulatorConfig()
        self._random = random.Random(self.config.seed)
        self._connected = False
        self._read_count = 0
        self._i_filt = 0.0
        self._last_elapsed: float | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True
        self._read_count = 0
        self._i_filt = 0.0
        self._last_elapsed = None

    async def close(self) -> None:
        self._connected = False

    def command_voltage(self, elapsed_seconds: float) -> float:
        """Commanded excitation (DAC). Public so tests can inspect the sweep."""
        cfg = self.config
        t = max(0.0, elapsed_seconds)
        if t < cfg.settle_seconds:
            return cfg.sweep_start_v
        t -= cfg.settle_seconds
        duration = cfg.sweep_seconds
        if duration <= 0:
            return cfg.sweep_end_v
        if cfg.sweep_repeat:
            t = t % duration
        elif t >= duration:
            return cfg.sweep_end_v
        frac = t / duration
        return cfg.sweep_start_v + (cfg.sweep_end_v - cfg.sweep_start_v) * frac

    def _apply_fault(self, elapsed: float, index: int) -> FaultKind | None:
        cfg = self.config
        if cfg.mode is not SimulatorMode.FAULT or elapsed < cfg.fault_start_s:
            return None
        kind = cfg.fault_kind
        if kind is FaultKind.NONE:
            return None
        if kind is FaultKind.DROPOUT and cfg.dropout_every_n > 0:
            if (index + 1) % cfg.dropout_every_n == 0:
                return FaultKind.DROPOUT
            return None
        if kind is FaultKind.UNSTABLE:
            return FaultKind.UNSTABLE
        # Other faults: after start, every other sample so some valid frames still flow
        if (index % 2) == 1:
            return kind
        return None

    async def read(self, elapsed_seconds: float) -> DriverReading:
        if not self._connected:
            raise RuntimeError("simulator is not connected")
        cfg = self.config
        index = self._read_count
        self._read_count += 1
        flags: list[str] = ["SIMULATED", cfg.mode.value.upper()]

        fault = self._apply_fault(elapsed_seconds, index)
        if fault in (FaultKind.DISCONNECTED, FaultKind.DROPOUT):
            flags.append("DROPOUT")
            if fault is FaultKind.DISCONNECTED:
                flags.append("DISCONNECTED")
            return DriverReading(
                ec=None,
                temperature=None,
                quality_flags=tuple(flags),
            )

        temperature = cfg.base_temperature + self._random.uniform(
            -cfg.temperature_noise, cfg.temperature_noise
        )
        if fault is FaultKind.TEMP_ABNORMAL:
            temperature = 95.0
            flags.append("OUT_OF_RANGE")
            flags.append("TEMP_ABNORMAL")

        v_cmd = self.command_voltage(elapsed_seconds)
        noise_scale = 4.0 if fault is FaultKind.UNSTABLE else 1.0
        v_meas = (
            v_cmd
            + cfg.voltage_offset_v
            + self._random.uniform(-cfg.voltage_noise_v, cfg.voltage_noise_v) * noise_scale
        )
        if fault is FaultKind.VOLTAGE_OOR:
            v_meas = -0.4
            flags.append("OUT_OF_RANGE")
            flags.append("VOLTAGE_OOR")

        g25 = cfg.nominal_conductance_s * (1.0 + cfg.drift_g_per_second * elapsed_seconds)
        g_t = g25 * (1.0 + cfg.alpha_per_c * (temperature - 25.0))
        i_target = g_t * v_cmd + cfg.current_offset_a + cfg.nonlinearity * (v_cmd * v_cmd)

        dt = (
            elapsed_seconds - self._last_elapsed
            if self._last_elapsed is not None
            else 1.0 / cfg.sample_rate_hz
        )
        self._last_elapsed = elapsed_seconds
        if cfg.polarization_tau_s > 1e-9 and dt > 0:
            alpha = min(1.0, dt / cfg.polarization_tau_s)
            self._i_filt += (i_target - self._i_filt) * alpha
            i_meas = self._i_filt
        else:
            self._i_filt = i_target
            i_meas = i_target
        i_meas += self._random.uniform(-cfg.current_noise_a, cfg.current_noise_a) * noise_scale

        if fault is FaultKind.CURRENT_ZERO:
            i_meas = 0.0
            flags.append("CURRENT_ZERO")
        if fault is FaultKind.ADC_SAT:
            i_meas = 0.05
            flags.append("OUT_OF_RANGE")
            flags.append("ADC_SAT")
        if fault is FaultKind.UNSTABLE:
            flags.append("UNSTABLE")

        return DriverReading(
            ec=None,
            temperature=round(temperature, 3),
            voltage_v=round(v_meas, 6),
            current_a=i_meas,
            quality_flags=tuple(flags),
        )
