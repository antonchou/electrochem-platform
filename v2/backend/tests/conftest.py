from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from ec.devices import DeviceStatus
from ec.frames import DeviceInfo, Reading
from ec.qc import QcConfig
from ec.settings import Settings

INFO = DeviceInfo("FAKE-01", "fw-test", "RS1000R_G1", 10.0, 0.2)


def reading(
    voltage_v: float | None = 0.1,
    current_a: float | None = 1e-4,
    temperature_c: float | None = 25.0,
    flags: tuple[str, ...] = (),
    device_seq: int | None = None,
) -> Reading:
    """缺省：G = 1e-3 S，Kcell = 1 时 κ25 = 1000 µS/cm。"""
    return Reading(voltage_v, current_a, temperature_c, flags, device_seq, None, INFO)


class FakeDevice:
    """测试用设备：测试往队列里放事件，Lab 按序消费。drain() 等到所有事件都处理完。"""

    kind = "fake"

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.prepared: list[tuple[str, float | None]] = []

    async def stream(self) -> AsyncIterator[Any]:
        while True:
            event = await self.queue.get()
            try:
                if event is None:
                    return
                yield event
            finally:
                self.queue.task_done()

    def prepare(self, sample_name: str, concentration_mmol_l: float | None) -> None:
        self.prepared.append((sample_name, concentration_mmol_l))

    async def send(self, *events: Any) -> None:
        for event in events:
            self.queue.put_nowait(event)
        await self.queue.join()

    async def connect(self) -> None:
        await self.send(DeviceStatus(True, "fake ready"))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "ec.db",
        static_dir=tmp_path / "no-dist",
        qc=QcConfig(window_s=5.0, min_points=5),
    )
