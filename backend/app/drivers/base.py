"""Common asynchronous boundary for mock and future hardware drivers."""

from __future__ import annotations

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

    @property
    def complete_for_conductivity(self) -> bool:
        return self.ec is not None and self.temperature is not None

    @property
    def complete_for_iv(self) -> bool:
        """I–V 链路完整性：U/I/T 齐备才可走计算链。"""
        return (
            self.voltage_v is not None
            and self.current_a is not None
            and self.temperature is not None
        )


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
