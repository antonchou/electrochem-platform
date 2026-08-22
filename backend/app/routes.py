"""路由：WebSocket 实时流 + REST 控制 + 历史查询与导出 + 备选公式拟合。"""

import asyncio
import datetime
import logging
import math
import os
import time
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect

from . import analysis, calibration, storage
from .broadcast import BroadcastHub
from .drivers import DeviceDriver, DriverReading, MockDevice, load_mock_config
from .persistence import PersistenceUnavailableError, persist
from .schemas import ControlResponse, ExperimentStartRequest, FitRequest
from .state import state
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
_control_lock = asyncio.Lock()

# 校准/激励默认值（取自设备配置，模拟"已标定"语义；真实硬件由校准记录提供）。
# start 未显式传参时使用默认，保证演示/测试链路有效；显式传 null 强制未校准。
_default_cell_constant_cm_inv: Optional[float] = None
_default_alpha_per_c: Optional[float] = None
_default_excitation_amplitude_v: Optional[float] = None
_default_excitation_frequency_hz: Optional[float] = None
_default_calibration_id: Optional[str] = None
_default_range_id: Optional[str] = None
_default_sensor_path_id: Optional[str] = None


async def broadcast(payload: dict) -> int:
    """把消息放入每个客户端的独立队列，返回接受消息的客户端数。"""
    return await _hub.publish(payload)


async def start_acquisition() -> None:
    """启动单一采集任务（幂等）。"""
    global _acquisition_task, _driver, _sample_period_seconds
    global _default_cell_constant_cm_inv, _default_alpha_per_c, _default_excitation_amplitude_v
    global _default_excitation_frequency_hz, _default_calibration_id
    global _default_range_id, _default_sensor_path_id
    if _acquisition_task is None or _acquisition_task.done():
        config = load_mock_config()
        _driver = MockDevice(config)
        await _driver.connect()
        _sample_period_seconds = 1.0 / config.sample_rate_hz
        _default_cell_constant_cm_inv = config.cell_constant_per_cm
        _default_alpha_per_c = config.alpha_per_c
        _default_excitation_amplitude_v = config.excitation_voltage_v
        _default_excitation_frequency_hz = config.excitation_frequency_hz
        _default_calibration_id = config.calibration_id
        _default_range_id = config.range_id
        _default_sensor_path_id = config.sensor_path_id
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
                if not reading.complete_for_iv:
                    logger.warning(
                        "设备读数不完整，仍记录质量帧: flags=%s",
                        reading.quality_flags,
                    )
                frame = _reading_to_frame(reading, elapsed)
                # 先落库再推送：客户端收到帧时，该帧必然已进入持久化队列（避免停止时丢帧）
                _enqueue_frame(frame)
                await broadcast(frame)
            await asyncio.sleep(_sample_period_seconds)
        except asyncio.CancelledError:
            break
        except PersistenceUnavailableError:
            logger.exception("持久化写入器不可用，当前实验切换为 error")
            exp_id = state.experiment_db_id
            changed = await state.fail()
            if changed and exp_id is not None:
                try:
                    await persist.finish_experiment(exp_id, "error")
                except Exception:
                    logger.exception("无法把持久化故障写入实验记录")
            await broadcast(_status_payload("error"))
            await asyncio.sleep(0.1)
        except Exception:
            # 单个循环出错不终止采集任务（如单次广播失败），但必须留下日志避免静默丢数据
            logger.exception("采集循环异常")
            await asyncio.sleep(0.1)


def _status_payload(status: str) -> dict:
    payload = {"message_type": "status", "status": status}
    if state.experiment_uid:
        payload["experiment_uid"] = state.experiment_uid
    return payload


def _calibration_is_expired() -> bool:
    """按 calibration_valid_until_utc 判断校准是否过期；无有效期视为未过期。"""
    until = state.calibration_valid_until_utc
    if not until:
        return False
    try:
        expires = datetime.datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc) > expires


def _reading_to_frame(reading: DriverReading, elapsed: float) -> dict:
    """把设备读数组装成协议 V2 帧。

    原始 U/I/T 来自驱动（Raw）；G/κ(T)/κ25 由软件计算层
    （app.measurement + app.calibration）得出，不依赖驱动。
    在线协议只输出 V2 规范字段；V1 兼容仅存在于历史数据库读取层。
    """
    flags = list(reading.quality_flags)
    computed = calibration.compute_iv(
        voltage_raw_v=reading.voltage_raw_v,
        current_raw_a=reading.current_raw_a,
        temperature_raw_c=reading.temperature_raw_c,
        cell_constant_cm_inv=state.cell_constant_cm_inv,
        alpha_per_c=state.alpha_per_c,
        compensation_model=state.compensation_model or calibration.DEFAULT_COMPENSATION_MODEL,
        calibration_expired=_calibration_is_expired(),
        extra_flags=tuple(flags),
    )
    flags = list(computed["quality_flags"])

    frame: dict = {
        "message_type": "measurement",
        "schema_version": "2.0",
        "seq_no": state.next_seq(),
        "timestamp_utc": _utc_now(),
        "monotonic_ms": int(time.monotonic() * 1000),
        "t_seconds": round(elapsed, 2),
        "status": "running",
        "experiment_uid": state.experiment_uid,
        # Raw：质量帧也显式保留键，缺测用 null，不能退化成 V1 帧。
        "voltage_raw_v": reading.voltage_raw_v,
        "current_raw_a": reading.current_raw_a,
        "temperature_raw_c": reading.temperature_raw_c,
        # Calibrated / Derived：不可用时显式为 null，并由 quality_flags 说明原因。
        "voltage_cal_v": None,
        "current_cal_a": None,
        "conductance_s": computed.get("conductance_s"),
        "kappa_t_us_cm": computed.get("kappa_t_us_cm"),
        "kappa_25_us_cm": computed.get("kappa_25_us_cm"),
        # Configuration / Trace：字段始终存在，值可空但语义不能消失。
        "sensor_path_id": state.sensor_path_id,
        "calibration_id": state.calibration_id,
        "cell_constant_cm_inv": state.cell_constant_cm_inv,
        "calibration_valid_until_utc": state.calibration_valid_until_utc,
        "excitation_frequency_hz": state.excitation_frequency_hz,
        "excitation_amplitude_v": state.excitation_amplitude_v,
        "range_id": state.range_id,
        "compensation_model": state.compensation_model,
        "alpha_per_c": state.alpha_per_c,
        "quality_flags": flags,
    }
    if state.concentration_mmol_l is not None:
        frame["concentration_mmol_l"] = state.concentration_mmol_l
    return frame


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _frame_to_row(frame: dict) -> dict:
    """把一帧实时数据转成 raw_frames 行（含溯源、配置与 I–V 原始/派生量）。"""
    return {
        "experiment_id": state.experiment_db_id,
        "sample_id": state.sample_id,
        "sensor_path_id": frame.get("sensor_path_id") or state.sensor_path_id,
        "seq_no": frame.get("seq_no"),
        "timestamp_utc": frame.get("timestamp_utc"),
        "monotonic_ms": frame.get("monotonic_ms"),
        "t_seconds": frame.get("t_seconds"),
        "schema_version": frame.get("schema_version"),
        # 在线 V2 不再写 V1 别名；该列仅供旧数据库迁移/历史读取。
        "legacy_ec_us_cm": None,
        "temperature_raw_c": frame.get("temperature_raw_c"),
        "voltage_raw_v": frame.get("voltage_raw_v"),
        "current_raw_a": frame.get("current_raw_a"),
        "voltage_cal_v": frame.get("voltage_cal_v"),
        "current_cal_a": frame.get("current_cal_a"),
        "conductance_s": frame.get("conductance_s"),
        "kappa_t_us_cm": frame.get("kappa_t_us_cm"),
        "kappa_25_us_cm": frame.get("kappa_25_us_cm"),
        "k25": frame.get("kappa_25_us_cm"),
        "excitation_frequency_hz": frame.get("excitation_frequency_hz"),
        "excitation_amplitude_v": frame.get("excitation_amplitude_v"),
        "range_id": frame.get("range_id"),
        "compensation_model": frame.get("compensation_model"),
        "alpha_per_c": frame.get("alpha_per_c"),
        "calibration_id": frame.get("calibration_id") or state.calibration_id,
        "cell_constant_cm_inv": frame.get("cell_constant_cm_inv"),
        "calibration_valid_until_utc": frame.get("calibration_valid_until_utc"),
        "quality_flags": "|".join(frame.get("quality_flags") or ()) or None,
        "status": state.status,
    }


def _enqueue_frame(frame: dict) -> None:
    """后台异步落库（仅当前有实验上下文时）。"""
    if state.experiment_db_id is not None:
        persist.enqueue_frame(_frame_to_row(frame))


@router.websocket("/ws/stream")
async def ws_stream(ws: WebSocket) -> None:
    """实时数据流订阅端：连接即订阅广播（数据由单一采集任务生成并推送）。

    服务端不在此处采集/落库（P1-1 修复），仅把连接加入广播集合，
    直到客户端断开。running 期间数据帧与状态帧均由 broadcast 送达。
    """
    await _hub.connect(ws)
    try:
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
    async with _control_lock:
        if persist.failed:
            raise HTTPException(status_code=503, detail="persistence writer is unavailable")
        if state.status == "running":
            return ControlResponse(ok=False, status=state.status, message="实验已在进行中")

        sample_id = body.sample_id or "SAMPLE"
        sensor_path_id = body.sensor_path_id or _default_sensor_path_id or "EC_IV_CELL_MOCK"
        title = body.title or "不同溶液导电性相对比较"
        uid = f"EXP-{datetime.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:4]}"

        # 校准/激励默认值：未显式传入时取设备配置（模拟"已标定"）；
        # 显式传 null 则保持 None → 采集链标记 UNCALIBRATED，不伪造电导率。
        supplied = body.model_fields_set
        cell_constant_from_default = "cell_constant_cm_inv" not in supplied
        cell_constant_cm_inv = (
            _default_cell_constant_cm_inv
            if cell_constant_from_default
            else body.cell_constant_cm_inv
        )
        alpha_per_c = (
            _default_alpha_per_c if "alpha_per_c" not in supplied else body.alpha_per_c
        )
        calibration_id = (
            body.calibration_id
            if "calibration_id" in supplied
            else (_default_calibration_id if cell_constant_from_default else None)
        )
        compensation_model = body.compensation_model or calibration.DEFAULT_COMPENSATION_MODEL
        excitation_amplitude_v = (
            _default_excitation_amplitude_v
            if "excitation_amplitude_v" not in supplied
            else body.excitation_amplitude_v
        )
        excitation_frequency_hz = (
            _default_excitation_frequency_hz
            if "excitation_frequency_hz" not in supplied
            else body.excitation_frequency_hz
        )
        range_id = _default_range_id if "range_id" not in supplied else body.range_id
        if cell_constant_cm_inv is not None and not calibration_id:
            raise HTTPException(
                status_code=422,
                detail="calibration_id is required when cell_constant_cm_inv is available",
            )

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
                "calibration_id": calibration_id,
                "cell_constant_cm_inv": cell_constant_cm_inv,
                "calibration_valid_until_utc": body.calibration_valid_until_utc,
                "alpha_per_c": alpha_per_c,
                "compensation_model": compensation_model,
                "excitation_frequency_hz": excitation_frequency_hz,
                "excitation_amplitude_v": excitation_amplitude_v,
                "range_id": range_id,
            },
        )

        try:
            ok = await state.start(
                sample_id=sample_id,
                sensor_path_id=sensor_path_id,
                concentration_mmol_l=body.concentration_mmol_l,
                title=title,
                experiment_db_id=exp_id,
                experiment_uid=uid,
                calibration_id=calibration_id,
                cell_constant_cm_inv=cell_constant_cm_inv,
                calibration_valid_until_utc=body.calibration_valid_until_utc,
                alpha_per_c=alpha_per_c,
                compensation_model=compensation_model,
                excitation_frequency_hz=excitation_frequency_hz,
                excitation_amplitude_v=excitation_amplitude_v,
                range_id=range_id,
            )
        except Exception:
            await persist.finish_experiment(exp_id, "error")
            raise
        if not ok:
            await persist.finish_experiment(exp_id, "error")
            return ControlResponse(ok=False, status=state.status, message="实验已在进行中")

    await broadcast(_status_payload("running"))
    return ControlResponse(
        ok=True,
        status="running",
        experiment_id=exp_id,
        sample_id=sample_id,
    )


@router.post("/api/experiment/stop")
async def stop() -> ControlResponse:
    async with _control_lock:
        changed = await state.stop()
        exp_id = state.experiment_db_id
        if changed and exp_id is not None:
            # 仅在 running→stopped 时结束实验：重复 stop 不再刷新 ended_at_utc
            try:
                await persist.flush()  # 确保在途帧先落库
                await persist.finish_experiment(exp_id, "stopped")
            except Exception:
                logger.exception("停止实验时持久化写入器不可用")
                await state.fail()
                await broadcast(_status_payload("error"))
                return ControlResponse(
                    ok=False,
                    status="error",
                    experiment_id=exp_id,
                    message="持久化写入失败，实验已进入错误状态",
                )
            await broadcast(_status_payload(state.status))
        return ControlResponse(
            ok=True,
            status=state.status,
            experiment_id=exp_id,
            message=None if changed else "当前没有运行中的实验",
        )


@router.post("/api/experiment/reset")
async def reset() -> ControlResponse:
    async with _control_lock:
        exp_id = state.experiment_db_id
        previous_status = state.status
        persistence_error: Exception | None = None
        if exp_id is not None:
            try:
                await persist.flush()
            except Exception as exc:
                logger.exception("重置实验时持久化写入器不可用")
                persistence_error = exc
            try:
                if previous_status == "running":
                    # 运行中被打断 → aborted（与 SRS 状态机语义一致），而非 idle
                    await persist.finish_experiment(exp_id, "aborted")
                elif previous_status == "error":
                    await persist.finish_experiment(exp_id, "error")
            except Exception as exc:
                logger.exception("无法写入重置前的实验终态")
                persistence_error = persistence_error or exc
        await state.reset()
        await broadcast(_status_payload("idle"))
        return ControlResponse(
            ok=persistence_error is None,
            status="idle",
            message=(
                None
                if persistence_error is None
                else "内存状态已重置，但持久化不可用；请恢复存储并重启服务"
            ),
        )


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "experiment": state.status}


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
async def export_json(exp_id: int) -> dict:
    """导出完整实验（元信息 + 全部帧）为 JSON。"""
    exp = await asyncio.to_thread(storage.get_experiment, exp_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    frames: list[dict] = []
    offset = 0
    page_size = 100_000
    while True:
        page = await asyncio.to_thread(
            storage.get_frames,
            exp_id,
            limit=page_size,
            offset=offset,
        )
        frames.extend(page)
        if len(page) < page_size:
            break
        offset += len(page)
    exp["frames"] = frames
    return exp


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
    return {"best": results[0]["model"] if results else None, "models": results}


# ---------- 调试接口（仅模拟源使用；用于验收 F08/F09/F10/P04） ----------


def _require_debug_enabled() -> None:
    enabled = os.environ.get("EC_ENABLE_DEBUG_ENDPOINTS", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        # 对生产调用方隐藏调试面；测试/演示须显式启用。
        raise HTTPException(status_code=404, detail="not found")


@router.post("/api/debug/bad-frame", dependencies=[Depends(_require_debug_enabled)])
async def inject_bad_frame() -> dict:
    """推送一条数值合法但协议过时的 V1 帧，验证在线端拒绝降级（F10）。"""
    await broadcast({"timestamp": 1.0, "ec": 1413.0, "temperature": 25.0, "status": "running"})
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
        frame = generate_frame(i * 0.1, experiment_uid=state.experiment_uid or "DEBUG-BURST")
        sent += await broadcast(frame)
        if i % 200 == 199:
            await asyncio.sleep(0.002)
    return {"ok": True, "sent": sent}
