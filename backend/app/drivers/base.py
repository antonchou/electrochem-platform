"""Common asynchronous boundary for mock and future hardware drivers."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DriverReading:
    """One unprocessed device reading.

    pH is reserved for a later sensor slice. The I–V measurement chain
    (REQ-M-001) needs raw voltage/current/temperature: voltage_v / current_a /
    temperature are the immutable raw quantities; ec (if set) is a compatible
    alias for the derived κ25 and must not be treated as a raw hardware value.
    """

    ec: float | None
    temperature: float | None
    ph: float | None = None
    voltage_v: float | None = None
    current_a: float | None = None
    quality_flags: tuple[str, ...] = ()

    # 完整性按“有限数”判定：NaN/inf（如温度探头读失败）等同缺失，整帧跳过，
    # 否则 NaN 会被 SQLite 存成 NULL、撞上 temperature_raw NOT NULL，让整批落库失败并永久降级。

    @property
    def complete_for_conductivity(self) -> bool:
        return _finite(self.ec) and _finite(self.temperature)

    @property
    def complete_for_iv(self) -> bool:
        """I–V 链路完整性：U/I/T 齐备且为有限数才可走计算链。"""
        return _finite(self.voltage_v) and _finite(self.current_a) and _finite(self.temperature)


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


@dataclass(frozen=True, slots=True, kw_only=True)
class DriverConfig:
    """各驱动 config 共有的采样率、计算参数与协议/校准元数据。

    routes 直接读这些字段组帧、写校准记录；各驱动按需覆盖默认值。
    calibration_claimed=None 表示驱动未声明是否已校准，由 calibration_id 推导。
    """

    sample_rate_hz: float = 10.0
    cell_constant_per_cm: float = 1.0
    alpha_per_c: float = 0.02
    excitation_frequency_hz: float = 1000.0  # 交流激励频率 Hz；0 = 直流
    excitation_amplitude_v: float = 1.0
    compensation_model: str = "linear_alpha"
    device_id: str = "UNKNOWN"
    firmware_version: str = "0.1.0"
    range_id: str = "UNKNOWN"
    calibration_id: str | None = None
    calibration_standard: str | None = None
    calibration_lot: str | None = None
    calibration_claimed: bool | None = None

    def __post_init__(self) -> None:
        # 子类是 slots dataclass，无参 super() 不可用，须显式调用 DriverConfig.__post_init__(self)
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.cell_constant_per_cm <= 0:
            raise ValueError("cell_constant_per_cm must be positive")


class DeviceDriver(ABC):
    """Minimal lifecycle shared by mock and real acquisition adapters."""

    config: DriverConfig

    @property
    @abstractmethod
    def connected(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def read(self, elapsed_seconds: float) -> DriverReading:
        raise NotImplementedError
