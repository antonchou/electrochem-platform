"""设备：推模式的异步数据流。设备按自己的节拍产出读数，主机来一帧处理一帧。

三种设备的统一接口：
    kind: str
    stream() -> AsyncIterator[Reading | DeviceStatus]   连接状态与设备日志也从这里出来
    prepare(sample_name, concentration_mmol_l)          开始测量前的钩子；只有模拟器用它换溶液

- SimulatedCell：按固件的测量方式模拟导电池（方波幅值、采样电阻、DS18B20 量化、固件的质量标志逻辑）
- SerialDevice：读 ESP32 固件的串口 JSON 帧（需要 pyserial）
- ReplayDevice：按原节拍回放 capture_serial.py 保存的 .jsonl 采集文件
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Protocol

from .chemistry import kcl_kappa25_us_cm
from .frames import DeviceInfo, Reading, SequenceCheck, parse_line
from .settings import Settings

logger = logging.getLogger("ec.devices")

RETRY_S = 2.0
MAX_LINE_BYTES = 4096  # 固件一帧约 300 字节；攒到这么长还没换行就是杂讯，丢弃
# 打开串口后发给 MicroPython 的按键：Ctrl-C 停掉在跑的程序（固件会先让电池回 0 V），Ctrl-B 退出 raw REPL，
# Ctrl-D 软重启并重新运行 main.py。Thonny 连接时会中断 main.py，断开后板子停在 REPL，不这样做就收不到帧；
# MicroPython 只在普通 REPL 下软重启才运行 main.py，所以 Ctrl-B 不能省。
RESTART_FIRMWARE = b"\x03\x02\x04"


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    connected: bool
    message: str | None = None


Event = Reading | DeviceStatus


class Device(Protocol):
    kind: str

    def stream(self) -> AsyncIterator[Event]: ...

    def prepare(self, sample_name: str, concentration_mmol_l: float | None) -> None: ...


# ---------------------------------------------------------------- 模拟器

FAULTS = ("none", "air", "no_temp", "drift", "noisy", "dropout", "reversed")


class SimulatedCell:
    """模拟「ESP32 + 导电池」。电极放进什么溶液由 prepare() 按样品浓度（KCl 模型）决定。

    物理：幅值 A 的方波加在「导电池 Rc + 采样电阻 Rs」串联回路上，
    I = A / (Rc + Rs)，U = I·Rc，Rc = Kcell真 / κ(T)。Kcell真 故意与标称值不同，
    这样「未标定 → 标定 → 读数回到标准液标称值」在演示里看得出来。

    故障（set_fault）：air 电极出水（开路）/ no_temp 温度探头掉线 / drift 读数每分钟漂 2% /
    noisy 噪声放大 20 倍 / dropout 每 4 帧掉一帧 / reversed 接线反了（U、I 为负）。
    """

    kind = "sim"
    SHUNT_OHM = 1000.0
    AMPLITUDE_V = 0.2
    WATER_KAPPA25_US_CM = 1.5  # 去离子水本底

    def __init__(
        self,
        rate_hz: float = 2.0,
        seed: int | None = None,
        cell_constant_per_cm: float = 1.02,
        alpha_per_c: float = 0.0195,
        settle_s: float = 1.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.period_s = 1.0 / rate_hz
        self.settle_s = settle_s  # 换溶液后读数趋稳的时间常数
        self.cell_constant_per_cm = cell_constant_per_cm
        self.alpha_per_c = alpha_per_c
        self._random = random.Random(seed)
        self._clock = clock
        self._t0: float | None = None
        self._seq = 0
        self.fault = "none"
        self._fault_since = 0.0
        self._from = self._to = 1413.0
        self._changed_at = 0.0
        self.info = DeviceInfo(
            device_id="SIM-IV-01",
            firmware_version="sim-2",
            range_id="RS1000R_G1",
            excitation_frequency_hz=10.0,
            excitation_amplitude_v=self.AMPLITUDE_V,
        )

    def _now(self) -> float:
        clock = self._clock or asyncio.get_running_loop().time
        now = clock()
        if self._t0 is None:
            self._t0 = now
        return now - self._t0

    def prepare(self, sample_name: str, concentration_mmol_l: float | None) -> None:
        if concentration_mmol_l is not None:
            self.set_solution(self.WATER_KAPPA25_US_CM + kcl_kappa25_us_cm(concentration_mmol_l))

    def set_solution(self, kappa25_us_cm: float) -> None:
        now = self._now()
        self._from = self.solution_kappa25(now)
        self._to = kappa25_us_cm
        self._changed_at = now

    def set_fault(self, fault: str) -> None:
        if fault not in FAULTS:
            raise ValueError(f"unknown fault {fault!r}; expected one of {', '.join(FAULTS)}")
        self.fault = fault
        self._fault_since = self._now()

    def solution_kappa25(self, t: float) -> float:
        settle = math.exp(-(t - self._changed_at) / self.settle_s)
        kappa = self._to + (self._from - self._to) * settle
        if self.fault == "drift":
            kappa *= 1 + 0.02 * (t - self._fault_since) / 60.0
        return kappa

    def temperature(self, t: float) -> float:
        """约 24.6 °C 的室温，缓慢起伏 ±0.15 °C，按 DS18B20 的 0.0625 °C 分辨率量化。"""
        raw = 24.6 + 0.15 * math.sin(2 * math.pi * t / 600.0) + self._random.gauss(0, 0.02)
        return round(raw / 0.0625) * 0.0625

    def sample(self, t: float) -> Reading:
        """t 秒时的一帧；质量标志与固件判定一致。"""
        self._seq += 1
        base = dict(device_seq=self._seq, device_ms=int(t * 1000), info=self.info)
        if self.fault == "dropout" and self._seq % 4 == 0:
            return Reading(None, None, self.temperature(t), ("DROPOUT",), **base)
        temperature = self.temperature(t)
        kappa_t = 0.0 if self.fault == "air" else (
            self.solution_kappa25(t) * (1 + self.alpha_per_c * (temperature - 25.0))
        )
        cell_ohm = self.cell_constant_per_cm / (kappa_t * 1e-6) if kappa_t > 0 else math.inf
        current = self.AMPLITUDE_V / (cell_ohm + self.SHUNT_OHM)
        voltage = self.AMPLITUDE_V if math.isinf(cell_ohm) else current * cell_ohm
        noise = 20.0 if self.fault == "noisy" else 1.0
        voltage += self._random.gauss(0, noise * (2e-6 + 5e-5 * voltage))
        current += self._random.gauss(0, noise * (2e-6 + 5e-5 * current * self.SHUNT_OHM)) / self.SHUNT_OHM
        if self.fault == "reversed":
            voltage, current = -voltage, -current
        flags = []
        if abs(current) * self.SHUNT_OHM < 50e-6:
            flags.append("OPEN_CIRCUIT")
        elif abs(voltage) < 0.01 * self.AMPLITUDE_V:
            flags.append("SHORT_CIRCUIT")
        if self.fault == "no_temp":
            temperature = None
            flags.append("TEMP_INVALID")
        return Reading(voltage, current, temperature, tuple(flags), **base)

    async def stream(self) -> AsyncIterator[Event]:
        loop = asyncio.get_running_loop()
        yield DeviceStatus(True, "模拟导电池已就绪")
        deadline = loop.time()
        while True:
            yield self.sample(self._now())
            deadline += self.period_s
            delay = deadline - loop.time()
            if delay < -self.period_s:
                deadline = loop.time()  # 落后超过一个周期：重新对齐，不补帧
            await asyncio.sleep(max(delay, 0.0))


# ---------------------------------------------------------------- 串口

class SerialDevice:
    """ESP32 固件的串口帧。设备日志行（"# ..."）作为状态消息上报，前端能直接看到器件自检结果。

    每次打开串口都先让固件软重启（RESTART_FIRMWARE），所以每次连接都从器件自检开始，设备序号从 1 起。
    """

    kind = "serial"

    def __init__(self, port: str, baud: int = 115200, open_port: Callable[[], Any] | None = None) -> None:
        self.port = port
        self.baud = baud
        self._open_port = open_port or self._open_pyserial

    def _open_pyserial(self) -> Any:
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("缺少 pyserial：pip install pyserial") from exc
        return serial.Serial(self.port, self.baud, timeout=1.0)

    def prepare(self, sample_name: str, concentration_mmol_l: float | None) -> None:
        pass

    async def stream(self) -> AsyncIterator[Event]:
        while True:
            try:
                port = await asyncio.to_thread(self._open_port)
            except Exception as exc:  # noqa: BLE001 - 任何打开失败都按「未连接，稍后重试」处理
                yield DeviceStatus(False, f"打不开串口 {self.port}：{exc}")
                await asyncio.sleep(RETRY_S)
                continue
            yield DeviceStatus(True, f"串口 {self.port} 已打开，正在重启固件")
            sequence = SequenceCheck()
            pending = b""
            try:
                await asyncio.to_thread(port.write, RESTART_FIRMWARE)
                while True:
                    raw = await asyncio.to_thread(port.readline)
                    if not raw:
                        continue  # 读超时：设备可能在自检或重启，继续等
                    # pyserial 的超时按整次调用计：帧恰好在超时边界到达时会先返回半行，攒齐换行再解析
                    pending += raw
                    if not pending.endswith(b"\n"):
                        if len(pending) > MAX_LINE_BYTES:
                            pending = b""
                        continue
                    line, pending = pending, b""
                    kind, payload = parse_line(line.decode("utf-8", errors="replace"))
                    if kind == "frame":
                        yield _with_sequence_flags(payload, sequence)
                    elif kind == "log":
                        yield DeviceStatus(True, payload)
            except Exception as exc:  # noqa: BLE001 - 串口拔掉等：上报后重连
                yield DeviceStatus(False, f"串口断开：{exc}")
            finally:
                try:
                    port.close()
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.sleep(RETRY_S)


def _with_sequence_flags(reading: Reading, sequence: SequenceCheck) -> Reading:
    extra = sequence.check(reading.device_seq)
    return replace(reading, flags=reading.flags + extra) if extra else reading


# ---------------------------------------------------------------- 回放

class ReplayDevice:
    """回放 .jsonl 采集文件（capture_serial.py 的输出，每行一帧固件 JSON）。帧间隔取设备时钟差 / speed。"""

    kind = "replay"
    MAX_GAP_S = 5.0  # 设备重启或长时间断线造成的大间隔，回放时压到这么长

    def __init__(self, path: Path, speed: float = 1.0, loop: bool = False) -> None:
        self.path = Path(path)
        self.speed = speed
        self.loop = loop

    def prepare(self, sample_name: str, concentration_mmol_l: float | None) -> None:
        pass

    def load(self) -> list[Reading]:
        readings = []
        with self.path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                kind, payload = parse_line(line)
                if kind == "frame":
                    readings.append(payload)
        return readings

    async def stream(self) -> AsyncIterator[Event]:
        try:
            readings = await asyncio.to_thread(self.load)
        except OSError as exc:
            yield DeviceStatus(False, f"打不开回放文件：{exc}")
            return
        if not readings:
            yield DeviceStatus(False, f"回放文件里没有设备帧：{self.path.name}")
            return
        yield DeviceStatus(True, f"回放 {self.path.name}（{len(readings)} 帧，{self.speed:g}×）")
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        first = True
        while True:
            sequence = SequenceCheck()
            previous_ms: int | None = None
            for reading in readings:
                if not first:
                    gap = 1.0  # 缺设备时钟、或循环回到文件开头时按 1 s 算
                    if previous_ms is not None and reading.device_ms is not None:
                        gap = (reading.device_ms - previous_ms) / 1000.0
                    deadline += min(max(gap, 0.0), self.MAX_GAP_S) / self.speed
                    await asyncio.sleep(max(deadline - loop.time(), 0.0))
                first = False
                previous_ms = reading.device_ms
                yield _with_sequence_flags(reading, sequence)
            if not self.loop:
                yield DeviceStatus(False, "回放结束")
                return


def make_device(settings: Settings) -> Device:
    if settings.device == "serial":
        return SerialDevice(settings.serial_port, settings.serial_baud)
    if settings.device == "replay":
        assert settings.replay_path is not None
        return ReplayDevice(settings.replay_path, settings.replay_speed, settings.replay_loop)
    return SimulatedCell(settings.sim_rate_hz, settings.sim_seed, settle_s=settings.sim_settle_s)
