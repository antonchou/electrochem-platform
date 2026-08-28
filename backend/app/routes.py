"""路由：WebSocket 实时流 + REST 控制 + 历史查询与导出 + 备选公式拟合。"""

import asyncio
import datetime
import json
import logging
import math
import os
import time
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect

from . import analysis, measurement, stability, storage
from .broadcast import BroadcastHub
from .drivers import (
    CsvPlaybackConfig,
    CsvPlaybackDriver,
    DeviceDriver,
    MockDevice,
    SimulatorDriver,
    load_mock_config,
    load_simulator_config,
)
from .persistence import persist
from .schemas import ControlResponse, CurrentExperimentResponse, ExperimentStartRequest, FitRequest
from .state import DEFAULT_SAMPLE_ID, DEFAULT_SENSOR_PATH_ID, state
from .stream import generate_frame

router = APIRouter()

logger = logging.getLogger("app.routes")

# 活跃 WebSocket 使用独立有界队列发送，慢客户端不得阻塞采集循环。
# 20_000 与前端最大点数一致，可承载 P04 的 10_000 点 burst，同时保持内存有界。
_hub = BroadcastHub(queue_size=20_000)

# 单一后台采集任务（P1-1 修复）：采集/落库与连接数无关，所有客户端收到同一帧广播
_acquisition_task: Optional[asyncio.Task] = None
_driver: Optional[DeviceDriver] = None
_sample_period_seconds = 0.1
_lifecycle_lock = asyncio.Lock()
_persist_notice_sent = False
_quiet_incomplete_flags: set[tuple[str, ...]] = set()

PERSIST_DEGRADED_MESSAGE = (
    "落库失败：实时曲线仍在更新，但历史和导出将缺帧。请重启后端恢复落库。"
)


async def broadcast(payload: dict) -> int:
    """把消息放入每个客户端的独立队列，返回接受消息的客户端数。"""
    return await _hub.publish(payload)


def _build_driver() -> tuple[DeviceDriver, float]:
    """按环境变量选择采集驱动。业务层只依赖 DeviceDriver，不分支模拟/真机。

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


async def start_acquisition() -> None:
    """启动单一采集任务（幂等）。"""
    global _acquisition_task, _driver, _sample_period_seconds
    if _acquisition_task is None or _acquisition_task.done():
        _driver, _sample_period_seconds = _build_driver()
        await _driver.connect()
        _acquisition_task = asyncio.create_task(_acquisition_loop())


async def stop_acquisition() -> None:
    """停止采集任务。"""
    global _acquisition_task, _driver
    if _acquisition_task is not None:
        _acquisition_task.cancel()
        try:
            await _acquisition_task
        except asyncio.CancelledError:
            pass
        _acquisition_task = None
    if _driver is not None:
        await _driver.close()
        _driver = None
    await _hub.close_all(code=1001, reason="server shutdown")


def _measurement_params() -> dict:
    """取当前驱动的 I–V 计算参数（电池常数/温补系数/协议元数据）。

    校准溯源字段来自驱动 config，不再默认写成 KCl 1413。
    """
    config = getattr(_driver, "config", None)
    cell = getattr(config, "cell_constant_per_cm", 1.0)
    alpha = getattr(config, "alpha_per_c", 0.02)
    amplitude = getattr(config, "excitation_amplitude_v", None)
    if amplitude is None:
        amplitude = getattr(config, "excitation_voltage_v", 1.0)
    driver_cal_id = getattr(config, "calibration_id", None)
    if isinstance(driver_cal_id, str):
        driver_cal_id = driver_cal_id.strip() or None
    claimed = getattr(config, "calibration_claimed", None)
    if claimed is None:
        claimed = bool(driver_cal_id) and driver_cal_id != "UNCALIBRATED"
    cal_id = state.calibration_id if state.calibration_id is not None else driver_cal_id
    return {
        "cell_constant_per_cm": cell,
        "alpha_per_c": alpha,
        "device_id": getattr(config, "device_id", "MOCK-IV-01"),
        "firmware_version": getattr(config, "firmware_version", "0.1.0"),
        "range_id": getattr(config, "range_id", "WIDE"),
        "excitation_frequency_hz": getattr(config, "excitation_frequency_hz", 1000.0),
        "excitation_amplitude_v": amplitude,
        "compensation_model": getattr(config, "compensation_model", "linear_alpha"),
        "driver_calibration_id": driver_cal_id,
        "calibration_id": cal_id,
        "calibration_standard": getattr(config, "calibration_standard", None),
        "calibration_lot": getattr(config, "calibration_lot", None),
        "calibration_claimed": bool(claimed),
        "calibration_mode": getattr(config, "calibration_mode", None)
        or ("cell_constant" if claimed else "none"),
    }


def _join_flags(*parts: str | None) -> str | None:
    tokens: list[str] = []
    for part in parts:
        if not part:
            continue
        for token in str(part).split("|"):
            if token and token not in tokens:
                tokens.append(token)
    return "|".join(tokens) if tokens else None


async def _acquisition_loop() -> None:
    """后台采集循环：实验 running 期间按配置频率读取 → 落库 → 广播。

    无论有没有浏览器连接都会持续生成并落库（解决"无连接时漏采"）；
    多连接也只产生一套数据并写入同一实验（解决"多连接重复采集"）。
    """
    driver = _driver
    if driver is None:
        raise RuntimeError("acquisition driver is not configured")

    while True:
        try:
            if state.status == "running":
                experiment_db_id = state.experiment_db_id
                elapsed = state.elapsed()
                reading = await driver.read(elapsed)
                # 真实硬件读取可能让出事件循环较长时间。读取期间若 stop/reset/新一轮 start，
                # 当前读数属于旧会话，必须丢弃，不能以 running 状态写入或推送。
                if (
                    state.status != "running"
                    or state.experiment_db_id != experiment_db_id
                ):
                    continue
                if not reading.complete_for_conductivity and not reading.complete_for_iv:
                    _log_incomplete_reading(reading.quality_flags)
                    await asyncio.sleep(_sample_period_seconds)
                    continue
                frame = _build_frame(elapsed, reading)
                # 先入队再推送。入队失败（持久化已降级）仍广播，并打 PERSIST_DROPPED。
                accepted = _enqueue_frame(frame)
                if not accepted:
                    flags = frame.get("quality_flags") or ""
                    frame["quality_flags"] = (
                        f"{flags}|PERSIST_DROPPED" if flags else "PERSIST_DROPPED"
                    )
                    await _notify_persist_degraded()
                await broadcast(frame)
            await asyncio.sleep(_sample_period_seconds)
        except asyncio.CancelledError:
            break
        except Exception:
            # 单个循环出错不终止采集任务（如单次广播失败），但必须留下日志避免静默丢数据
            logger.exception("采集循环异常")
            await asyncio.sleep(0.1)


def _build_frame(elapsed: float, reading) -> dict:
    """把一次读数组装成协议帧。

    - 读数含 U/I/T（complete_for_iv）：走软件计算链 G=I/U → κ(T) → κ25，
      帧补全 I–V 字段；旧字段 ec 仍是 κ25 的兼容别名，temperature 仍为温度。
    - 读数只有 ec/temperature（旧驱动或 dropout 后仍完整）：回退 V1 简化帧。
    """
    params = _measurement_params()
    uncal = None if params.get("calibration_claimed") else "UNCALIBRATED"
    quality = _join_flags("|".join(reading.quality_flags), uncal)
    base = {
        "timestamp": round(elapsed, 2),
        "temperature": reading.temperature,
        "status": "running",
        "quality_flags": quality,
    }
    if reading.complete_for_iv:
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
            return {
                **base,
                "ec": None,
                "schema_version": 2,
                "device_id": params["device_id"],
                "firmware_version": params["firmware_version"],
                "range_id": params["range_id"],
                "calibration_id": params["calibration_id"],
                "excitation_frequency_hz": params["excitation_frequency_hz"],
                "excitation_amplitude_v": params["excitation_amplitude_v"],
                "compensation_model": params["compensation_model"],
                "voltage_raw_v": reading.voltage_v,
                "current_raw_a": reading.current_a,
                "temperature_raw_c": reading.temperature,
                "conductance_s": None,
                "kappa_t_us_cm": None,
                "kappa_25_us_cm": None,
                "quality_flags": _join_flags(quality, "COMPUTE_INVALID"),
            }
        return {
            **base,
            "ec": round(result.kappa_25_us_cm, 1),
            "schema_version": 2,
            "device_id": params["device_id"],
            "firmware_version": params["firmware_version"],
            "range_id": params["range_id"],
            "calibration_id": params["calibration_id"],
            "excitation_frequency_hz": params["excitation_frequency_hz"],
            "excitation_amplitude_v": params["excitation_amplitude_v"],
            "compensation_model": params["compensation_model"],
            "voltage_raw_v": reading.voltage_v,
            "current_raw_a": reading.current_a,
            "temperature_raw_c": reading.temperature,
            "conductance_s": result.conductance_s,
            "kappa_t_us_cm": result.kappa_t_us_cm,
            "kappa_25_us_cm": result.kappa_25_us_cm,
        }
    return {**base, "ec": reading.ec}


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _frame_to_row(frame: dict) -> dict:
    """把一帧实时数据转成 raw_frames 行（带溯源字段与 I–V 计算列）。"""
    return {
        "experiment_id": state.experiment_db_id,
        "sample_id": state.sample_id,
        "sensor_path_id": state.sensor_path_id,
        "seq_no": state.next_seq(),
        "timestamp_utc": _utc_now(),
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


def _enqueue_frame(frame: dict) -> bool:
    """后台异步落库。无实验上下文视为无需落库（True）；拒收返回 False。"""
    if state.experiment_db_id is None:
        return True
    return persist.enqueue_frame(_frame_to_row(frame))


def _persist_degraded_payload() -> dict:
    """状态帧：晚连/刷新客户端与一次性广播共用同一文案。"""
    snap = persist.snapshot()
    payload: dict = {
        "status": state.status,
        "message": PERSIST_DEGRADED_MESSAGE,
        "persistence": snap["persistence"],
    }
    if state.experiment_db_id is not None:
        payload["experiment_id"] = state.experiment_db_id
    return payload


async def _notify_persist_degraded() -> None:
    """采集拒帧时广播告警；同一次降级只主动推一次，避免 10Hz 刷屏。

    晚连客户端不依赖这次广播：WebSocket 握手后若仍降级会补发同一状态帧。
    """
    global _persist_notice_sent
    if _persist_notice_sent:
        return
    _persist_notice_sent = True
    await broadcast(_persist_degraded_payload())


def _log_incomplete_reading(flags: tuple[str, ...]) -> None:
    """EOF/EMPTY 只 info 一次；其它不完整读数每次 warning。"""
    quiet = "EOF" in flags or "EMPTY" in flags
    if quiet:
        if flags in _quiet_incomplete_flags:
            return
        _quiet_incomplete_flags.add(flags)
        logger.info("回放结束或无数据，后续静默: flags=%s", flags)
        return
    logger.warning("设备读数不完整，跳过本周期: flags=%s", flags)


def _reset_persist_notice() -> None:
    global _persist_notice_sent
    _persist_notice_sent = False
    _quiet_incomplete_flags.clear()


async def _flush_frames_best_effort() -> bool:
    """Flush queued frames. False if persistence already failed or flush raises."""
    if persist.degraded:
        return False
    try:
        await persist.flush()
        return not persist.degraded
    except Exception:
        logger.exception("持久化 flush 失败")
        return False


async def _finish_experiment_best_effort(exp_id: int, status: str) -> None:
    try:
        await persist.finish_experiment(exp_id, status)
    except Exception:
        logger.exception("finish_experiment(%s) 失败", status)


@router.websocket("/ws/stream")
async def ws_stream(ws: WebSocket) -> None:
    """实时数据流订阅端：连接即订阅广播（数据由单一采集任务生成并推送）。

    服务端不在此处采集/落库（P1-1 修复），仅把连接加入广播集合，
    直到客户端断开。running 期间数据帧与状态帧均由 broadcast 送达。
    """
    await _hub.connect(ws)
    try:
        # 晚连/刷新：一次性告警可能已发出且当时无订阅者，连接时补发。
        if persist.degraded:
            await _hub.send_to(ws, _persist_degraded_payload())
        # 客户端不发送业务消息，这里阻塞等待断连信号
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await _hub.disconnect(ws)


@router.post("/api/experiment/start")
async def start(body: ExperimentStartRequest | None = None) -> ControlResponse:
    body = body or ExperimentStartRequest()
    async with _lifecycle_lock:
        if state.status == "running":
            return ControlResponse(ok=False, status=state.status, message="实验已在进行中")

        # 停止后续跑同一条实验（同样品）；改样品编号则开新实验。
        resume_sample = body.sample_id or state.sample_id
        if (
            state.status == "stopped"
            and state.experiment_db_id is not None
            and resume_sample == state.sample_id
        ):
            exp_id = state.experiment_db_id
            reopened = await persist.reopen_experiment(exp_id)
            if reopened and await state.resume():
                _reset_persist_notice()
                await broadcast({"status": "running", "experiment_id": exp_id})
                if persist.degraded:
                    await _notify_persist_degraded()
                return ControlResponse(
                    ok=True,
                    status="running",
                    experiment_id=exp_id,
                    sample_id=state.sample_id,
                    resumed=True,
                    persistence="degraded" if persist.degraded else None,
                    message=PERSIST_DEGRADED_MESSAGE if persist.degraded else None,
                )

        sample_id = body.sample_id or DEFAULT_SAMPLE_ID
        sensor_path_id = body.sensor_path_id or DEFAULT_SENSOR_PATH_ID
        title = body.title or "不同溶液导电性相对比较"
        uid = f"EXP-{datetime.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:4]}"
        params = _measurement_params()
        env_cal = os.environ.get("EC_CALIBRATION_ID", "").strip()
        cal_id = env_cal or params.get("driver_calibration_id")

        # 实验与样品在一个 SQLite 事务中创建。数据库失败时内存态不会进入 running，
        # 并发 start 也由本锁串行化，避免遗留空的 running/idle 历史记录。
        exp_id = await persist.create_experiment_with_sample(
            experiment_id=uid,
            title=title,
            operator=body.operator,
            objective=body.objective,
            sample_id=sample_id,
            sensor_path_id=sensor_path_id,
            concentration_mmol_l=body.concentration_mmol_l,
            metadata={
                "sample_id": sample_id,
                "sensor_path_id": sensor_path_id,
                "concentration_mmol_l": body.concentration_mmol_l,
                "calibration_id": cal_id,
            },
        )

        try:
            if cal_id:
                claimed = bool(params.get("calibration_claimed"))
                await persist.insert_calibration_record(
                    experiment_id=exp_id,
                    calibration_id=cal_id,
                    sensor_path_id=sensor_path_id,
                    mode=params.get("calibration_mode") or ("cell_constant" if claimed else "none"),
                    standard=params.get("calibration_standard"),
                    lot=params.get("calibration_lot"),
                    coeff_value=params["cell_constant_per_cm"] if claimed else None,
                    coeff_json={
                        "cell_constant_per_cm": params["cell_constant_per_cm"],
                        "alpha_per_c": params["alpha_per_c"],
                        "compensation_model": params["compensation_model"],
                        "excitation_frequency_hz": params["excitation_frequency_hz"],
                        "excitation_amplitude_v": params["excitation_amplitude_v"],
                        "device_id": params["device_id"],
                        "firmware_version": params["firmware_version"],
                        "range_id": params["range_id"],
                        "calibration_claimed": claimed,
                    },
                )
            ok = await state.start(
                sample_id=sample_id,
                sensor_path_id=sensor_path_id,
                title=title,
                experiment_db_id=exp_id,
                experiment_uid=uid,
                calibration_id=cal_id,
            )
        except Exception:
            await persist.finish_experiment(exp_id, "error")
            raise
        if not ok:
            await persist.finish_experiment(exp_id, "error")
            return ControlResponse(ok=False, status=state.status, message="实验已在进行中")

        _reset_persist_notice()
        await broadcast({"status": "running", "experiment_id": exp_id})
        if persist.degraded:
            await _notify_persist_degraded()
        return ControlResponse(
            ok=True,
            status="running",
            experiment_id=exp_id,
            sample_id=sample_id,
            resumed=False,
            persistence="degraded" if persist.degraded else None,
            message=PERSIST_DEGRADED_MESSAGE if persist.degraded else None,
        )


@router.post("/api/experiment/stop")
async def stop() -> ControlResponse:
    async with _lifecycle_lock:
        changed = await state.stop()
        exp_id = state.experiment_db_id
        status = state.status
        persist_ok = True
        if changed and exp_id is not None:
            # 仅在 running→stopped 时结束实验：重复 stop 不再刷新 ended_at_utc
            persist_ok = await _flush_frames_best_effort()
            if persist_ok:
                await _compute_and_store_qc(exp_id)
            await _finish_experiment_best_effort(exp_id, "stopped")
            payload: dict = {"status": status, "experiment_id": exp_id}
            if not persist_ok:
                payload["message"] = PERSIST_DEGRADED_MESSAGE
                payload["persistence"] = "degraded"
            await broadcast(payload)
        # 幂等 stop（本来就没在跑）不是错误，不带 message，避免前端当失败横幅。
        message = PERSIST_DEGRADED_MESSAGE if changed and not persist_ok else None
        return ControlResponse(
            ok=True,
            status=status,
            experiment_id=exp_id,
            message=message,
            persistence=None if persist_ok else "degraded",
        )


async def _compute_and_store_qc(exp_id: int) -> None:
    """实验停止时，对已落库的 κ25 帧做一次判稳，把 QC 结果写回 samples（REQ-D-003）。

    纯增量：帧不足或计算异常时跳过写 QC，不影响 stop 主流程。
    """
    try:
        rows = await asyncio.to_thread(storage.get_recent_frames, exp_id, limit=500)
        if not rows:
            return
        kappa25, flags = stability.qc_series_from_frames(rows)
        if len(kappa25) < 3:
            return
        result = stability.check_stability(kappa25, quality_flags=flags)
        sample_id = rows[-1].get("sample_id") or state.sample_id
        sensor_path_id = rows[-1].get("sensor_path_id") or state.sensor_path_id
        await persist.update_sample_qc(
            experiment_id=exp_id,
            sample_id=sample_id,
            sensor_path_id=sensor_path_id,
            qc_status=result.status,
            qc_reason=result.reason,
            representative_value=result.representative_value,
            k25_median=result.median,
            k25_mean=result.mean,
            k25_sd=result.std,
        )
    except Exception:
        logger.exception("QC 计算失败，跳过（不影响 stop 主流程）")


@router.post("/api/experiment/reset")
async def reset() -> ControlResponse:
    async with _lifecycle_lock:
        exp_id = state.experiment_db_id
        was_running = state.status == "running"
        persist_ok = True
        if exp_id is not None:
            persist_ok = await _flush_frames_best_effort()
            if was_running:
                # 运行中被打断 → aborted（与 SRS 状态机语义一致），而非 idle
                await _finish_experiment_best_effort(exp_id, "aborted")
        await state.reset()
        _reset_persist_notice()
        payload: dict = {"status": "idle"}
        message = None
        if not persist_ok:
            payload["message"] = PERSIST_DEGRADED_MESSAGE
            payload["persistence"] = "degraded"
            message = PERSIST_DEGRADED_MESSAGE
        await broadcast(payload)
        return ControlResponse(
            ok=True,
            status="idle",
            message=message,
            persistence=None if persist_ok else "degraded",
        )


@router.get("/api/experiment/current")
async def current_experiment() -> CurrentExperimentResponse:
    """前端重连后用来恢复 experiment_id / 样品号，避免导出按钮消失。"""
    snap = persist.snapshot()
    degraded = snap["persistence"] == "degraded"
    return CurrentExperimentResponse(
        status=state.status,
        experiment_id=state.experiment_db_id,
        sample_id=state.sample_id if state.experiment_db_id is not None else None,
        experiment_uid=state.experiment_uid,
        persistence=snap["persistence"],
        message=PERSIST_DEGRADED_MESSAGE if degraded else None,
    )


@router.get("/health")
async def health() -> dict:
    snap = persist.snapshot()
    return {
        "status": "ok",
        "experiment": state.status,
        "persistence": snap["persistence"],
        "persistence_error": snap["persistence_error"],
    }


# ---------- Phase 7：历史查询与导出 ----------


@router.get("/api/experiments")
async def list_experiments() -> list[dict]:
    """历史实验列表（含每实验帧数）。"""
    return await asyncio.to_thread(storage.list_experiments)


@router.get("/api/experiments/{exp_id}")
async def experiment_detail(exp_id: int) -> dict:
    """实验详情：元信息 + 样品汇总。"""
    exp = await asyncio.to_thread(storage.get_experiment, exp_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    exp["samples"] = await asyncio.to_thread(storage.get_samples, exp_id)
    exp["frame_count"] = await asyncio.to_thread(storage.count_frames, exp_id)
    exp["calibrations"] = await asyncio.to_thread(storage.get_calibration_records, exp_id)
    exp["fits"] = await asyncio.to_thread(storage.get_fit_results, exp_id)
    return exp


@router.get("/api/experiments/{exp_id}/frames")
async def experiment_frames(
    exp_id: int,
    limit: Annotated[int, Query(ge=1, le=100_000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    rows = await asyncio.to_thread(storage.get_frames, exp_id, limit=limit, offset=offset)
    return {"frames": rows}


@router.get("/api/experiments/{exp_id}/export.csv")
async def export_csv(exp_id: int) -> Response:
    """导出原始帧为 CSV（Excel 可打开）。"""
    if await asyncio.to_thread(storage.get_experiment, exp_id) is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    csv_text = await asyncio.to_thread(storage.export_csv, exp_id)
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="experiment_{exp_id}.csv"'
        },
    )


@router.get("/api/experiments/{exp_id}/export.json")
async def export_json(exp_id: int) -> Response:
    """导出完整实验（元信息 + 全部帧）为 JSON。"""
    exp = await asyncio.to_thread(storage.get_experiment, exp_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    frames = await asyncio.to_thread(storage.get_frames, exp_id, limit=1_000_000)
    total = await asyncio.to_thread(storage.count_frames, exp_id)
    exp["frames"] = frames
    exp["truncated"] = total > len(frames)
    exp["frame_count_total"] = total
    return Response(
        content=json.dumps(exp, ensure_ascii=False),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="experiment_{exp_id}.json"'
        },
    )


# ---------- 备选公式拟合（M4 前置） ----------


@router.post("/api/analysis/fit")
async def fit(body: FitRequest) -> dict:
    """对传入数据点（如 EC-t）做备选公式拟合，按 R² 排序并返回拟合曲线。"""
    x, y = body.x, body.y
    if len(x) != len(y) or len(x) < 3:
        raise HTTPException(status_code=400, detail="至少需要 3 个且 x/y 等长的数据点")
    if any(not math.isfinite(v) for v in x) or any(not math.isfinite(v) for v in y):
        raise HTTPException(status_code=400, detail="数据点含非有限数值")

    results = await asyncio.to_thread(analysis.fit_all, x, y, body.models, body.x_axis)
    derived_path = None
    if body.experiment_id is not None:
        exp = await asyncio.to_thread(storage.get_experiment, body.experiment_id)
        if exp is None:
            raise HTTPException(status_code=404, detail="experiment not found")
        payload = {
            "experiment_id": body.experiment_id,
            "x_axis": body.x_axis,
            "best": results[0]["model"] if results else None,
            "models": results,
        }
        derived_path = await asyncio.to_thread(
            storage.write_fit_report, body.experiment_id, payload
        )
        sample_id = exp.get("sample_id")
        await persist.insert_fit_results(
            experiment_id=body.experiment_id,
            x_axis=body.x_axis,
            models=results,
            sample_id=sample_id,
            derived_path=derived_path,
        )
    return {
        "best": results[0]["model"] if results else None,
        "models": results,
        "derived_path": derived_path,
    }


# ---------- 调试接口（仅模拟源使用；用于验收 F08/F09/F10/P04） ----------


def _require_debug_enabled() -> None:
    enabled = os.environ.get("EC_ENABLE_DEBUG_ENDPOINTS", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        # 对生产调用方隐藏调试面；测试/演示须显式启用。
        raise HTTPException(status_code=404, detail="not found")


@router.post("/api/debug/bad-frame", dependencies=[Depends(_require_debug_enabled)])
async def inject_bad_frame() -> dict:
    """推送一条非法帧（ec 为非数值），验证前端容错不崩溃（F10）。"""
    await broadcast({"timestamp": 1.0, "ec": "abc", "temperature": 25.0, "status": "running"})
    return {"ok": True, "injected": "bad-frame"}


@router.post("/api/debug/close-connections", dependencies=[Depends(_require_debug_enabled)])
async def close_connections() -> dict:
    """强制关闭所有 WS 连接，验证前端断线检测与重连（F08/F09）。"""
    count = await _hub.close_all(code=1001, reason="debug close")
    return {"ok": True, "closed": count}


@router.post("/api/debug/burst", dependencies=[Depends(_require_debug_enabled)])
async def burst(count: Annotated[int, Query(ge=1, le=10_000)] = 10_000) -> dict:
    """快速推送 count 帧（默认 1 万），用于验证前端大点数负载与 30 分钟模拟（P03/P04）。

    仅广播、不落库、不消耗 seq：注入帧不进入 raw_frames（B-3 修复，保证原始数据纯净）。
    """
    sent = 0
    for i in range(count):
        frame = generate_frame(i * 0.1)
        sent += await broadcast(frame)
        if i % 200 == 199:
            await asyncio.sleep(0.002)
    return {"ok": True, "sent": sent}
