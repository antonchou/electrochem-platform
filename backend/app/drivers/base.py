"""Common asynchronous boundary for mock and future hardware drivers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DriverReading:
    """One unprocessed device reading.

    pH is reserved for a later sensor slice. The current conductivity protocol
    deliberately exposes only EC and temperature, but keeping the raw driver
    field here avoids changing the hardware boundary when pH support arrives.
    """

    ec: float | None
    temperature: float | None
    ph: float | None = None
    quality_flags: tuple[str, ...] = ()

    @property
    def complete_for_conductivity(self) -> bool:
        return self.ec is not None and self.temperature is not None


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
