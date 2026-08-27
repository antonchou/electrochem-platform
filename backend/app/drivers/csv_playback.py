"""CSV 回放驱动：把 echemdb 4 列数据（time_s, voltage_v, current, temperature_c）当作设备读数回放。

用于「数据接入系统测试」：验证真实数据能走通 Driver → 计算链 → 落库 → 广播 全链路。
注意：CSV 是 CV 循环伏安数据，电压列是电极电位（可为负），电流列可能是 A 或 A/m²。
本驱动如实回放原始值，不做换算；计算链对电压 ≤ 0 的帧会抛 ValueError（由上层捕获并标记
COMPUTE_INVALID），原始 U/I/T 仍落库（Raw 层不可变），Derived 层为 null。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from .base import DeviceDriver, DriverReading


@dataclass(frozen=True, slots=True)
class CsvPlaybackConfig:
    path: str
    sample_rate_hz: float = 10.0
    loop: bool = False
    speed: float = 1.0  # 1× realtime; 2 / 10 = accelerated. Pause = experiment stop.
    device_id: str = "CSV-PLAYBACK-01"
    firmware_version: str = "0.1.0"
    range_id: str = "CSV"
    cell_constant_per_cm: float = 1.0
    alpha_per_c: float = 0.02
    excitation_frequency_hz: float = 0.0
    excitation_amplitude_v: float = 1.0
    compensation_model: str = "linear_alpha"
    calibration_id: str | None = "UNCALIBRATED"
    calibration_standard: str | None = "playback: no calibration claim"
    calibration_lot: str | None = None
    calibration_claimed: bool = False

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.speed <= 0:
            raise ValueError("speed must be positive")


class CsvPlaybackDriver(DeviceDriver):
    """按 elapsed 时间回放 CSV 行；超过末尾返回 EOF 质量标志（loop 则循环）。

    CSV 列：time_s, voltage_v, current, temperature_c（echemdb 整理格式）。
    """

    def __init__(self, config: CsvPlaybackConfig) -> None:
        self.config = config
        self._connected = False
        self._rows: List[Tuple[float, float, float, float]] = []
        self._max_t = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        path = Path(self.config.path)
        if not path.exists():
            raise FileNotFoundError(f"CSV not found: {path}")
        rows: List[Tuple[float, float, float, float]] = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    t = float(row["time_s"])
                    v = float(row["voltage_v"])
                    i = float(row["current"])
                    temp = float(row["temperature_c"])
                except (KeyError, ValueError):
                    continue
                rows.append((t, v, i, temp))
        if not rows:
            raise ValueError(f"no valid rows in {path}")
        rows.sort(key=lambda r: r[0])
        self._rows = rows
        self._max_t = rows[-1][0]
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    async def read(self, elapsed_seconds: float) -> DriverReading:
        if not self._connected:
            raise RuntimeError("csv playback driver is not connected")
        rows = self._rows
        if not rows:
            return DriverReading(ec=None, temperature=None, quality_flags=("CSV", "EMPTY"))
        t_eff = elapsed_seconds * self.config.speed
        if t_eff > self._max_t:
            if not self.config.loop:
                return DriverReading(ec=None, temperature=None, quality_flags=("CSV", "EOF"))
            t_eff = t_eff % (self._max_t + 1e-9)
        # 取时间戳 ≤ t_eff 的最近一行（线性外推需求简单，直接取最近）
        chosen = rows[0]
        for r in rows:
            if r[0] > t_eff:
                break
            chosen = r
        _, v, i, temp = chosen
        return DriverReading(
            ec=None,
            temperature=temp,
            voltage_v=v,
            current_a=i,
            quality_flags=("CSV", "PLAYBACK"),
        )
