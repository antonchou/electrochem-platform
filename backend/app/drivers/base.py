"""Common asynchronous boundary for mock and future hardware drivers.

驱动层（Driver）只负责采集原始 U/I/T 与质量标志，**不计算任何派生量**；
G / κ(T) / κ25 由软件计算层（app.measurement / app.calibration）统一得出。
这样 Mock 与未来硬件共用同一条计算链，接入硬件时只替换数据源。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DriverReading:
    """One raw device reading（电极 I–V 链路，SRS v0.2 Raw 层）。

    字段命名与数据分层对齐：voltage_raw_v / current_raw_a / temperature_raw_c。
    缺测（如 DROPOUT）时对应字段为 None，由 quality_flags 说明原因。
    """

    voltage_raw_v: float | None = None
    current_raw_a: float | None = None
    temperature_raw_c: float | None = None
    quality_flags: tuple[str, ...] = ()
    # 后续传感通道扩展位（当前电导率实验不使用）
    ph: float | None = None

    @property
    def complete_for_conductivity(self) -> bool:
        """I–V 测量可用：U/I/T 齐全且 U 非零（U=0 为开路/无激励，无法定义电导）。"""
        return (
            self.voltage_raw_v is not None
            and self.current_raw_a is not None
            and self.temperature_raw_c is not None
            and self.voltage_raw_v != 0.0
        )

    @property
    def complete_for_iv(self) -> bool:
        """与 complete_for_conductivity 同义（I–V 链路判定）。"""
        return self.complete_for_conductivity


class DeviceDriver(ABC):
    """Minimal lifecycle shared by mock and real acquisition adapters."""

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
