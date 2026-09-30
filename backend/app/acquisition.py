"""单一后台采集任务：按节拍读驱动 → 组帧（I–V 计算链）→ 续跑去重 → 落库 → 广播。

采集与 WebSocket 连接数无关（P1-1）：无论有没有浏览器连接都持续采数、落库；
多连接也只产生一套数据、写入同一实验，所有客户端收到同一帧广播。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from . import measurement, storage
from .broadcast import hub
from .drivers import (
    CsvPlaybackConfig,
    CsvPlaybackDriver,
    DeviceDriver,
    DriverConfig,
    DriverReading,
    MockDevice,
    SimulatorDriver,
    load_mock_config,
    load_simulator_config,
)
from .persistence import PERSIST_DEGRADED_MESSAGE, persist
from .state import state

logger = logging.getLogger("app.acquisition")

ACQUISITION_ERROR_MESSAGE = "采集异常：设备读数失败，正在自动重试（详见后端日志）。"
# 采集连续失败时的退避上限与完整堆栈的最小间隔（09-30 审查 R3-11）
_BACKOFF_MAX_S = 5.0
_ERROR_LOG_INTERVAL_S = 30.0

# 帧与校准记录共用的协议/激励元数据字段（均直接取自驱动 config）
FRAME_META_FIELDS = (
    "device_id",
    "firmware_version",
    "range_id",
    "excitation_frequency_hz",
    "excitation_amplitude_v",
    "compensation_model",
)
# 没有 config 的驱动（测试替身等）按通用缺省元数据组帧
_DEFAULT_DRIVER_CONFIG = DriverConfig()


def build_driver() -> tuple[DeviceDriver, float]:
    """按环境变量选择采集驱动，返回 (驱动, 采样周期秒)。业务层只依赖 DeviceDriver。

    - EC_DRIVER=mock（缺省）→ MockDevice，生产 kiosk 行为不变
    - EC_DRIVER=simulator → SimulatorDriver（电压扫描 + stable/realistic/fault）
    - EC_DRIVER=csv + EC_CSV_PATH → CsvPlaybackDriver（历史 4 列 CSV 回放）
    """
    kind = os.environ.get("EC_DRIVER", "mock").strip().lower()
    if kind == "csv":
        path = os.environ.get("EC_CSV_PATH", "")
        if not path:
            raise ValueError("EC_CSV_PATH is required when EC_DRIVER=csv")
        kwargs: dict = {"path": path}
        cell = os.environ.get("EC_CELL_CONSTANT", "").strip()
        if cell:
            kwargs["cell_constant_per_cm"] = float(cell)
        rate = os.environ.get("EC_CSV_SAMPLE_RATE_HZ", "").strip()
        if rate:
            kwargs["sample_rate_hz"] = float(rate)
        speed = os.environ.get("EC_CSV_SPEED", "").strip()
        if speed:
            kwargs["speed"] = float(speed)
        cfg = CsvPlaybackConfig(**kwargs)
        return CsvPlaybackDriver(cfg), 1.0 / cfg.sample_rate_hz
    if kind == "simulator":
        config = load_simulator_config()
        return SimulatorDriver(config), 1.0 / config.sample_rate_hz
    if kind in ("mock", ""):
        config = load_mock_config()
        return MockDevice(config), 1.0 / config.sample_rate_hz
    raise ValueError(
        f"unknown EC_DRIVER={kind!r}; expected mock, simulator, or csv "
        "(future ADS1256 adapter should register here without changing routes/WS)"
    )


def _join_flags(*parts: str | None) -> str | None:
    tokens: list[str] = []
    for part in parts:
        if not part:
            continue
        for token in str(part).split("|"):
            if token and token not in tokens:
                tokens.append(token)
    return "|".join(tokens) if tokens else None


def _frame_to_row(frame: dict) -> dict:
    """把一帧实时数据转成 raw_frames 行（带溯源字段与 I–V 计算列）。"""
    return {
        "experiment_id": state.experiment_db_id,
        "sample_id": state.sample_id,
        "sensor_path_id": state.sensor_path_id,
        "seq_no": state.next_seq(),
        "timestamp_utc": storage.utc_now(),
        "monotonic_ms": int(time.monotonic() * 1000),
        "t_seconds": frame.get("timestamp"),
        "ec_raw": frame.get("ec"),
        "temperature_raw": frame.get("temperature"),
        "k25": frame.get("kappa_25_us_cm"),
        "quality_flags": frame.get("quality_flags"),
        "status": state.status,
        "voltage_raw_v": frame.get("voltage_raw_v"),
        "current_raw_a": frame.get("current_raw_a"),
        "conductance_s": frame.get("conductance_s"),
        "kappa_t_us_cm": frame.get("kappa_t_us_cm"),
        "kappa_25_us_cm": frame.get("kappa_25_us_cm"),
        "schema_version": frame.get("schema_version"),
        "device_id": frame.get("device_id"),
        "firmware_version": frame.get("firmware_version"),
        "range_id": frame.get("range_id"),
        "calibration_id": frame.get("calibration_id") or state.calibration_id,
        "excitation_frequency_hz": frame.get("excitation_frequency_hz"),
        "excitation_amplitude_v": frame.get("excitation_amplitude_v"),
        "compensation_model": frame.get("compensation_model"),
    }


class Acquisition:
    """单一后台采集任务及其运行期状态：驱动、节拍、续跑去重窗口、一次性告警闩锁。"""

    def __init__(self) -> None:
        self.driver: Optional[DeviceDriver] = None
        self.sample_period_s = 0.1
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        # 续跑去重基准：停止前最后一条落库帧的 (U, I, T) 原始三元组。
        # None 表示无待比对窗口（非续跑、或首帧已消费）。
        self._resume_boundary_raws: Optional[tuple] = None
        self._persist_notice_sent = False
        self._quiet_incomplete_flags: set[tuple[str, ...]] = set()

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        """启动单一采集任务（幂等，加锁防止并发重入双建任务）。"""
        async with self._lock:
            if self._task is None or self._task.done():
                # 构建（CSV 全文件读入 + 排序）与 connect（IO）都可能耗时，移出事件循环
                # 执行，避免阻塞所有 API/WS（T-09）
                self.driver, self.sample_period_s = await asyncio.to_thread(build_driver)
                await self.driver.connect()
                self._task = asyncio.create_task(self.run(), name="acquisition-loop")

    async def stop(self) -> None:
        """停止采集任务并关闭驱动。"""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self.driver is not None:
            await self.driver.close()
            self.driver = None

    # ---------- 采集循环 ----------

    async def run(self) -> None:
        """采集循环：实验 running 期间按配置频率读取 → 落库 → 广播。

        节拍按截止时刻调度（R3-10）：旧实现“处理完再固定 sleep 一个周期”，处理耗时累加进
        周期，实际采样率系统性偏低；Windows 上周期短于时钟分辨率（15.6ms）时 sleep 会被
        当作已到期立即返回，循环空转狂出帧。截止时刻逐周期推进，平均速率被钉死在配置值。

        读数持续抛错（如真实 ADC 掉线，R3-11）：指数退避到 5s，完整堆栈只在首次和之后每
        30s 各记一条，并向前端广播一次告警；恢复后记一条恢复日志。任何异常都不终止本任务。
        """
        driver = self.driver
        if driver is None:
            raise RuntimeError("acquisition driver is not configured")

        loop = asyncio.get_running_loop()
        deadline = loop.time()
        failures = 0
        last_error_log = float("-inf")
        while True:
            try:
                if state.status == "running":
                    await self._acquire_once(driver)
                    if failures:
                        logger.info("采集恢复：此前连续失败 %d 次", failures)
                # 实验停下来也结束本段失败：下次开始若仍失败，会重新告警
                failures = 0
                deadline = await self._sleep_until(loop, deadline + self.sample_period_s)
            except asyncio.CancelledError:
                break
            except Exception:
                failures += 1
                try:
                    now = loop.time()
                    if failures == 1 or now - last_error_log >= _ERROR_LOG_INTERVAL_S:
                        logger.exception("采集循环异常（连续第 %d 次）", failures)
                        last_error_log = now
                    if failures == 1:
                        await self._notify_acquisition_error()
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.debug("采集异常处理本身出错", exc_info=True)
                # 指数退避（指数封顶，防止长时间故障后 2**n 溢出为 float）
                backoff = self.sample_period_s * 2 ** min(failures - 1, 16)
                try:
                    await asyncio.sleep(min(backoff, _BACKOFF_MAX_S))
                except asyncio.CancelledError:
                    break
                deadline = loop.time()

    async def _sleep_until(self, loop: asyncio.AbstractEventLoop, deadline: float) -> float:
        """睡到截止时刻，返回实际采用的截止时刻。

        落后超过一个周期（读数阻塞、事件循环卡顿）则从当前时刻重新对齐，不补发积压的周期。
        """
        now = loop.time()
        if deadline < now - self.sample_period_s:
            deadline = now
        await asyncio.sleep(max(0.0, deadline - now))
        return deadline

    async def _acquire_once(self, driver: DeviceDriver) -> None:
        """读一次设备并处理：计算链 → 续跑去重 → 落库 → 广播。"""
        experiment_db_id = state.experiment_db_id
        elapsed = state.elapsed()
        reading = await driver.read(elapsed)
        # 真实硬件读取可能让出事件循环较长时间。读取期间若 stop/reset/新一轮 start，
        # 当前读数属于旧会话，必须丢弃，不能以 running 状态写入或推送。
        if state.status != "running" or state.experiment_db_id != experiment_db_id:
            return
        if not reading.complete_for_conductivity and not reading.complete_for_iv:
            self._log_incomplete_reading(reading.quality_flags)
            return
        frame = self._build_frame(elapsed, reading)
        if experiment_db_id is not None:
            # 数据帧自带所属实验：前端缓冲据此隔离实验，不依赖是否收到过状态帧
            # （后端重启、旁观端、断线重连都可能错过 running 广播，09-30 审查 #3）
            frame["experiment_id"] = experiment_db_id
        if self._consume_resume_duplicate(frame):
            # 续跑首帧重采样了停止前的边界读数 → 丢弃（不占 seq、不落库、不广播）
            return
        # 先入队再推送。入队失败（持久化已降级）仍广播，并打 PERSIST_DROPPED。
        if not self._enqueue_frame(frame):
            flags = frame.get("quality_flags") or ""
            frame["quality_flags"] = f"{flags}|PERSIST_DROPPED" if flags else "PERSIST_DROPPED"
            await self.notify_persist_degraded()
        await hub.publish(frame)

    def _enqueue_frame(self, frame: dict) -> bool:
        """后台异步落库。无实验上下文视为无需落库（True）；拒收返回 False。"""
        if state.experiment_db_id is None:
            return True
        return persist.enqueue_frame(_frame_to_row(frame))

    async def _notify_acquisition_error(self) -> None:
        """采集连续失败的第一次向前端广播告警（每段失败只推一次，恢复后再失败会再推）。

        旧实现前端只能等 3s 看门狗报“数据流超时”，看不出是设备读数在失败。
        """
        payload: dict = {"status": state.status, "message": ACQUISITION_ERROR_MESSAGE}
        if state.experiment_db_id is not None:
            payload["experiment_id"] = state.experiment_db_id
        await hub.publish(payload)

    def _log_incomplete_reading(self, flags: tuple[str, ...]) -> None:
        """EOF/EMPTY 只 info 一次；其它不完整读数每次 warning。"""
        quiet = "EOF" in flags or "EMPTY" in flags
        if quiet:
            if flags in self._quiet_incomplete_flags:
                return
            self._quiet_incomplete_flags.add(flags)
            logger.info("回放结束或无数据，后续静默: flags=%s", flags)
            return
        logger.warning("设备读数不完整，跳过本周期: flags=%s", flags)

    # ---------- 组帧 ----------

    def measurement_params(self) -> dict:
        """取当前驱动的 I–V 计算参数与协议/校准元数据（来源是驱动 config，不默认写成 KCl 1413）。"""
        config: DriverConfig = getattr(self.driver, "config", None) or _DEFAULT_DRIVER_CONFIG
        driver_cal_id = (config.calibration_id or "").strip() or None
        cal_id = state.calibration_id if state.calibration_id is not None else driver_cal_id
        claimed = config.calibration_claimed
        if claimed is None:
            # 驱动未显式声明时才从校准 id 推导（P2-5）：显式 calibration_claimed=False
            # 是"有编号但未校准"的声明，不得被覆盖——与"尊重显式 fault_kind"同一原则。
            # 溯源一致性：帧携带真实 calibration_id 时不得再标 UNCALIBRATED；
            # "UNCALIBRATED" 是驱动的"未校准"哨兵值，不算已声明（T-06）。
            claimed = bool(cal_id) and cal_id != "UNCALIBRATED"
        return {
            "cell_constant_per_cm": config.cell_constant_per_cm,
            "alpha_per_c": config.alpha_per_c,
            **{name: getattr(config, name) for name in FRAME_META_FIELDS},
            "driver_calibration_id": driver_cal_id,
            "calibration_id": cal_id,
            "calibration_standard": config.calibration_standard,
            "calibration_lot": config.calibration_lot,
            "calibration_claimed": claimed,
            "calibration_mode": "cell_constant" if claimed else "none",
        }

    def _build_frame(self, elapsed: float, reading: DriverReading) -> dict:
        """把一次读数组装成协议帧。

        - 读数含 U/I/T（complete_for_iv）：走软件计算链 G=I/U → κ(T) → κ25，
          帧补全 I–V 字段；旧字段 ec 仍是 κ25 的兼容别名，temperature 仍为温度。
        - 读数只有 ec/temperature（旧驱动或 dropout 后仍完整）：回退 V1 简化帧。
        """
        params = self.measurement_params()
        uncal = None if params["calibration_claimed"] else "UNCALIBRATED"
        quality = _join_flags("|".join(reading.quality_flags), uncal)
        frame: dict = {
            "timestamp": round(elapsed, 2),
            "temperature": reading.temperature,
            "status": "running",
            "quality_flags": quality,
        }
        if not reading.complete_for_iv:
            frame["ec"] = reading.ec
            return frame

        frame.update(
            schema_version=2,
            calibration_id=params["calibration_id"],
            voltage_raw_v=reading.voltage_v,
            current_raw_a=reading.current_a,
            temperature_raw_c=reading.temperature,
            **{name: params[name] for name in FRAME_META_FIELDS},
        )
        try:
            result = measurement.compute_chain(
                reading.voltage_v,
                reading.current_a,
                reading.temperature,
                params["cell_constant_per_cm"],
                params["alpha_per_c"],
            )
        except ValueError as exc:
            # CV 数据的电压是电极电位（可为负/零），不满足激励电压>0的物理前提。
            # 原始 U/I/T 仍落库（Raw 不可变），Derived 标记 COMPUTE_INVALID，不崩溃。
            logger.warning("计算链拒绝该帧: %s flags=%s", exc, reading.quality_flags)
            frame.update(
                ec=None,
                conductance_s=None,
                kappa_t_us_cm=None,
                kappa_25_us_cm=None,
                quality_flags=_join_flags(quality, "COMPUTE_INVALID"),
            )
            return frame
        frame.update(
            ec=round(result.kappa_25_us_cm, 1),
            conductance_s=result.conductance_s,
            kappa_t_us_cm=result.kappa_t_us_cm,
            kappa_25_us_cm=result.kappa_25_us_cm,
        )
        return frame

    # ---------- 落库降级告警 ----------

    def persist_degraded_payload(self) -> dict:
        """状态帧：晚连/刷新客户端与一次性广播共用同一文案。"""
        payload: dict = {
            "status": state.status,
            "message": PERSIST_DEGRADED_MESSAGE,
            "persistence": persist.snapshot()["persistence"],
        }
        if state.experiment_db_id is not None:
            payload["experiment_id"] = state.experiment_db_id
        return payload

    async def notify_persist_degraded(self) -> None:
        """采集拒帧时广播告警；同一次降级只主动推一次，避免 10Hz 刷屏。

        晚连客户端不依赖这次广播：WebSocket 握手后若仍降级会补发同一状态帧。
        """
        if self._persist_notice_sent:
            return
        self._persist_notice_sent = True
        await hub.publish(self.persist_degraded_payload())

    def reset_notices(self) -> None:
        """新实验/续跑/复位时重置一次性告警闩锁与静默的不完整读数记录。"""
        self._persist_notice_sent = False
        self._quiet_incomplete_flags.clear()

    # ---------- 续跑边界去重 ----------

    async def load_resume_boundary(self, exp_id: int) -> None:
        """装载续跑去重基准：停止前最后一条落库帧的 (U, I, T) 原始三元组。

        CSV 类确定性源按 elapsed 回放，续跑首帧会重新采样停止前的边界源行
        （elapsed 从停点续上），不去重就会以新 seq_no 重复落一条相同样本。
        Mock 类噪声源每次读数不同，不会命中比对。
        去重是尽力而为：读库失败只记告警、不去重，不能让续跑整体失败。
        """
        try:
            rows = await asyncio.to_thread(storage.get_recent_frames, exp_id, limit=1)
        except Exception:
            logger.warning("续跑去重基准装载失败，本次不去重", exc_info=True)
            rows = []
        if rows:
            r = rows[0]
            self._resume_boundary_raws = (
                r.get("voltage_raw_v"),
                r.get("current_raw_a"),
                r.get("temperature_raw"),
            )
        else:
            self._resume_boundary_raws = None

    def clear_resume_boundary(self) -> None:
        self._resume_boundary_raws = None

    def _consume_resume_duplicate(self, frame: dict) -> bool:
        """一次性窗口：续跑首帧若与停止前最后一条落库帧 U/I/T 完全一致则丢弃。

        只判第一帧——命中说明重采样到了边界源行；未命中（读数已前进）或
        已消费过则窗口关闭，后续保持帧语义不变（慢速源正常持有同值帧）。
        """
        ref = self._resume_boundary_raws
        if ref is None:
            return False
        self.clear_resume_boundary()
        raws = (
            frame.get("voltage_raw_v"),
            frame.get("current_raw_a"),
            frame.get("temperature_raw_c"),
        )
        return raws == ref


acquisition = Acquisition()
