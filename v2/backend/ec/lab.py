"""核心编排：消费设备数据流，维护「监视」与「记录」两种状态，批量落库，开始/停止测量，标定。

- 监视：设备一直出读数，全部推给前端（显示、看读数是否稳定），不落库。
- 记录：测量进行中，每帧编号、落库，并在尾部窗口上实时判稳。
- 落库：帧先进内存待写队列，每秒批量写一次；写失败的帧留着下次重试（有上限），数据库恢复后自动续上。
- 推送：先改状态、再发布消息；WS 新连接先收快照（见 hub.py），前端据有序消息重建，不需要另走 REST 对齐。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

from . import records
from .devices import Device, DeviceStatus
from .errors import Conflict, Invalid, Unavailable
from .frames import DeviceInfo, Reading, join_flags
from .hub import Hub
from .qc import QcPoint, QcResult, assess, config_dict
from .settings import Settings
from .store import FrameRow, Store

logger = logging.getLogger("ec.lab")

FLUSH_INTERVAL_S = 1.0
MAX_PENDING_FRAMES = 36_000  # 数据库持续不可写时最多在内存里留这么多帧（10 Hz 约 1 小时）
MAX_RECORDING_POINTS = 20_000  # 快照里当前测量的点数上限
MONITOR_POINTS = 600
DEVICE_RETRY_S = 2.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Lab:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        device: Device,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.store = store
        self.device = device
        self.clock = clock
        self.hub = Hub(self.snapshot)

        self.device_connected = False
        self.device_message: str | None = None
        self.device_info = DeviceInfo()
        self.calibration: dict[str, Any] | None = None
        self.measurement: dict[str, Any] | None = None
        self.last_finished: dict[str, Any] | None = None
        self.storage_error: str | None = None
        self.dropped_frames = 0

        self._lab_t0 = clock()
        self._measurement_t0 = 0.0
        self._seq = 0
        self._recording: deque[dict[str, Any]] = deque(maxlen=MAX_RECORDING_POINTS)
        self._qc_window: deque[QcPoint] = deque()
        self._live_qc: QcResult | None = None
        self._monitor: deque[dict[str, Any]] = deque(maxlen=MONITOR_POINTS)
        self._pending: list[FrameRow] = []
        self._control = asyncio.Lock()  # 开始 / 停止 / 标定互斥
        self._flush_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []

    # ------------------------------------------------------------ 生命周期
    async def start(self) -> None:
        # 上次进程没正常结束时留下的 running 测量：帧都在，标为 aborted（不判稳）
        for measurement_id in await asyncio.to_thread(self.store.running_measurement_ids):
            await asyncio.to_thread(self.store.finish_measurement, measurement_id, "aborted", utc_now(), None)
            logger.warning("测量 #%s 上次未正常结束，已标为 aborted", measurement_id)
        self.calibration = await asyncio.to_thread(self.store.latest_calibration)
        self._tasks = [asyncio.create_task(self._consume()), asyncio.create_task(self._flush_loop())]

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self.measurement is not None:
            measurement, self.measurement = self.measurement, None
            try:
                await self._flush()
                await asyncio.to_thread(self.store.finish_measurement, measurement["id"], "aborted", utc_now(), None)
            except Exception:  # noqa: BLE001 - 关停路径尽力而为；下次启动会把它标为 aborted
                logger.exception("关停时结束测量 #%s 失败", measurement["id"])

    # ------------------------------------------------------------ 设备流
    async def _consume(self) -> None:
        while True:
            try:
                async for event in self.device.stream():
                    if isinstance(event, DeviceStatus):
                        self._on_status(event)
                    else:
                        self._on_reading(event)
                self._on_status(DeviceStatus(False, self.device_message or "设备数据流已结束"))
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 设备层任何异常都不能让采集任务消失
                logger.exception("设备数据流异常，%.0f s 后重连", DEVICE_RETRY_S)
                self._on_status(DeviceStatus(False, f"设备异常：{exc}"))
                await asyncio.sleep(DEVICE_RETRY_S)

    def _on_status(self, status: DeviceStatus) -> None:
        self.device_connected = status.connected
        if status.message:
            self.device_message = status.message
        self._publish_state()

    def _on_reading(self, reading: Reading) -> None:
        changed = not self.device_connected
        self.device_connected = True
        if reading.info.device_id is not None and reading.info != self.device_info:
            self.device_info = reading.info
            changed = True
        if changed:
            self._publish_state()

        measurement = self.measurement
        if measurement is None:
            point = records.make_point(
                round(self.clock() - self._lab_t0, 3), reading.voltage_v, reading.current_a,
                reading.temperature_c, reading.flags, *self._current_parameters(),
            )
            self._monitor.append(point)
            self.hub.publish({"type": "reading", "measurement_id": None, "point": point})
            return

        self._seq += 1
        t_s = round(self.clock() - self._measurement_t0, 3)
        self._queue_frame(FrameRow(
            measurement["id"], self._seq, t_s, utc_now(), reading.device_seq, reading.device_ms,
            reading.voltage_v, reading.current_a, reading.temperature_c, join_flags(reading.flags),
        ))
        point = records.make_point(
            t_s, reading.voltage_v, reading.current_a, reading.temperature_c, reading.flags,
            measurement["cell_constant_per_cm"], measurement["alpha_per_c"], seq=self._seq,
        )
        self._recording.append(point)
        self._qc_window.append(records.qc_point(point))
        while self._qc_window[0].t_s < t_s - self.settings.qc.window_s:
            self._qc_window.popleft()
        self._live_qc = assess(self._qc_window, self.settings.qc)
        self.hub.publish({
            "type": "reading",
            "measurement_id": measurement["id"],
            "point": point,
            "qc": self._live_qc.as_dict(),
        })

    def _current_parameters(self) -> tuple[float, float]:
        """此刻开始一次测量会用的 (Kcell, α)：最新标定，没有就用标称值。"""
        kcell = self.calibration["cell_constant_per_cm"] if self.calibration else self.settings.nominal_cell_constant_per_cm
        return kcell, self.settings.alpha_per_c

    # ------------------------------------------------------------ 落库
    def _queue_frame(self, row: FrameRow) -> None:
        self._pending.append(row)
        overflow = len(self._pending) - MAX_PENDING_FRAMES
        if overflow > 0:
            del self._pending[:overflow]
            self.dropped_frames += overflow
            logger.error("待写帧超过上限，丢弃最旧的 %d 帧（累计 %d）", overflow, self.dropped_frames)

    async def _flush(self) -> None:
        async with self._flush_lock:
            if not self._pending:
                return
            batch, self._pending = self._pending, []
            try:
                await asyncio.to_thread(self.store.insert_frames, batch)
            except Exception as exc:
                self._pending = batch + self._pending
                if self.storage_error is None:
                    logger.exception("帧写入数据库失败，稍后重试")
                self.storage_error = f"数据库写入失败，{len(self._pending)} 帧待重试：{exc}"
                self._publish_state()
                raise
            if self.storage_error is not None:
                logger.info("数据库写入已恢复")
                self.storage_error = None
                self._publish_state()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL_S)
            with contextlib.suppress(Exception):
                await self._flush()

    # ------------------------------------------------------------ 测量
    async def start_measurement(
        self, sample_name: str, concentration_mmol_l: float | None, note: str | None
    ) -> dict[str, Any]:
        async with self._control:
            if self.measurement is not None:
                raise Conflict(f"测量 #{self.measurement['id']} 正在进行，先停止它")
            if not self.device_connected:
                raise Unavailable("设备未连接，无法开始测量")
            kcell, alpha = self._current_parameters()
            params = {
                "sample_name": sample_name,
                "concentration_mmol_l": concentration_mmol_l,
                "note": note,
                "started_at": utc_now(),
                "cell_constant_per_cm": kcell,
                "calibration_id": self.calibration["id"] if self.calibration else None,
                "alpha_per_c": alpha,
                "device_kind": self.device.kind,
                **self.device_info.as_dict(),
            }
            measurement_id = await asyncio.to_thread(self.store.create_measurement, params)
            self.device.prepare(sample_name, concentration_mmol_l)
            self.measurement = {"id": measurement_id, "status": "running", **params}
            self._measurement_t0 = self.clock()
            self._seq = 0
            self._recording.clear()
            self._qc_window.clear()
            self._live_qc = None
            self._publish_state()
            return dict(self.measurement)

    async def stop_measurement(self) -> dict[str, Any]:
        async with self._control:
            measurement = self.measurement
            if measurement is None:
                raise Conflict("没有进行中的测量")
            self.measurement = None  # 先停止记录，之后的读数回到监视
            try:
                await self._flush()
            except Exception as exc:
                self.measurement = measurement  # 写不进去就继续记录，别让测量卡在半结束状态
                raise Unavailable(f"数据库写入失败，测量仍在进行，请稍后再停止：{exc}") from exc
            result = await asyncio.to_thread(records.assess_measurement, self.store, measurement, self.settings.qc)
            qc = {**result.as_dict(), "config": config_dict(self.settings.qc)}
            await asyncio.to_thread(self.store.finish_measurement, measurement["id"], "completed", utc_now(), qc)
            finished = await asyncio.to_thread(self.store.get_measurement, measurement["id"])
            self.last_finished = finished
            self._monitor.clear()
            self._publish_state()
            return finished

    # ------------------------------------------------------------ 标定
    async def calibrate(self, entries: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
        async with self._control:
            if self.measurement is not None:
                raise Conflict("测量进行中不能更换标定，先停止测量")
            fit, points = await asyncio.to_thread(records.calibration_points, self.store, entries)
            if fit["n"] >= 2 and fit["r2"] is not None and fit["r2"] < 0:
                raise Invalid("各标准液点彼此矛盾（R² < 0），请检查标准液与测量是否对应")
            calibration = {
                "created_at": utc_now(),
                "cell_constant_per_cm": fit["cell_constant_per_cm"],
                "r2": fit["r2"],
                **{key: meta.get(key) for key in ("operator", "cell_id", "lot", "note")},
            }
            await asyncio.to_thread(self.store.create_calibration, calibration, points)
            self.calibration = await asyncio.to_thread(self.store.latest_calibration)
            self._publish_state()
            return {**self.calibration, "fit": fit}

    # ------------------------------------------------------------ 状态
    def state(self) -> dict[str, Any]:
        kcell, alpha = self._current_parameters()
        return {
            "device": {
                "kind": self.device.kind,
                "connected": self.device_connected,
                "message": self.device_message,
                "info": self.device_info.as_dict(),
            },
            "measurement": dict(self.measurement) if self.measurement else None,
            "last_finished": self.last_finished,
            "calibration": self.calibration,
            "cell_constant_per_cm": kcell,
            "alpha_per_c": alpha,
            "qc_config": config_dict(self.settings.qc),
            "storage_error": self.storage_error,
        }

    def snapshot(self) -> dict[str, Any]:
        recording = self.measurement is not None
        return {
            "type": "snapshot",
            "state": self.state(),
            "points": list(self._recording if recording else self._monitor),
            "qc": self._live_qc.as_dict() if recording and self._live_qc else None,
        }

    def _publish_state(self) -> None:
        self.hub.publish({"type": "state", "state": self.state()})
