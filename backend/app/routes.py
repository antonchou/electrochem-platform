"""路由：WebSocket 实时流 + REST 控制 + 历史查询与导出 + 备选公式拟合。"""

import asyncio
import datetime
import logging
import math
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect

from . import analysis, storage
from .broadcast import BroadcastHub
from .drivers import DeviceDriver, MockDevice, load_mock_config
from .persistence import persist
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


async def broadcast(payload: dict) -> int:
    """把消息放入每个客户端的独立队列，返回接受消息的客户端数。"""
    return await _hub.publish(payload)


async def start_acquisition() -> None:
    """启动单一采集任务（幂等）。"""
    global _acquisition_task, _driver, _sample_period_seconds
    if _acquisition_task is None or _acquisition_task.done():
        config = load_mock_config()
        _driver = MockDevice(config)
        await _driver.connect()
        _sample_period_seconds = 1.0 / config.sample_rate_hz
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
                elapsed = state.elapsed()
                reading = await driver.read(elapsed)
                if not reading.complete_for_conductivity:
                    logger.warning(
                        "设备读数不完整，跳过本周期: flags=%s",
                        reading.quality_flags,
                    )
                    await asyncio.sleep(_sample_period_seconds)
                    continue
                frame = {
                    "timestamp": round(elapsed, 2),
                    "ec": reading.ec,
                    "temperature": reading.temperature,
                    "status": "running",
                }
                # 先落库再推送：客户端收到帧时，该帧必然已进入持久化队列（避免停止时丢帧）
                _enqueue_frame(
                    frame["timestamp"],
                    frame["ec"],
                    frame["temperature"],
                    reading.quality_flags,
                )
                await broadcast(frame)
            await asyncio.sleep(_sample_period_seconds)
        except asyncio.CancelledError:
            break
        except Exception:
            # 单个循环出错不终止采集任务（如单次广播失败），但必须留下日志避免静默丢数据
            logger.exception("采集循环异常")
            await asyncio.sleep(0.1)


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _frame_to_row(
    t_seconds: float,
    ec: float,
    temperature: float,
    quality_flags: tuple[str, ...] = (),
) -> dict:
    """把一帧实时数据转成 raw_frames 行（带溯源字段）。"""
    return {
        "experiment_id": state.experiment_db_id,
        "sample_id": state.sample_id,
        "sensor_path_id": state.sensor_path_id,
        "seq_no": state.next_seq(),
        "timestamp_utc": _utc_now(),
        "monotonic_ms": int(time.monotonic() * 1000),
        "t_seconds": t_seconds,
        "ec_raw": ec,
        "temperature_raw": temperature,
        "k25": None,
        "quality_flags": "|".join(quality_flags) or None,
        "status": state.status,
    }


def _enqueue_frame(
    t_seconds: float,
    ec: float,
    temperature: float,
    quality_flags: tuple[str, ...] = (),
) -> None:
    """后台异步落库（仅当前有实验上下文时）。"""
    if state.experiment_db_id is not None:
        persist.enqueue_frame(
            _frame_to_row(t_seconds, ec, temperature, quality_flags)
        )


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
    # 预检查（真正的原子判定在 state.start 锁内）
    if state.status == "running":
        return ControlResponse(ok=False, status=state.status, message="实验已在进行中")

    # Phase 7：先建实验记录与样品记录（此时尚未进入 running，采集任务不会落库）
    sample_id = body.sample_id or "SAMPLE"
    sensor_path_id = body.sensor_path_id or "CM2_WIDE"
    title = body.title or "不同溶液导电性相对比较"
    uid = f"EXP-{datetime.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:4]}"
    exp_id = await persist.create_experiment(
        experiment_id=uid,
        title=title,
        operator=body.operator,
        objective=body.objective,
        sample_id=sample_id,
        sensor_path_id=sensor_path_id,
        metadata={
            "sample_id": sample_id,
            "sensor_path_id": sensor_path_id,
            "concentration_mmol_l": body.concentration_mmol_l,
        },
    )

    # 原子进入 running 并绑定新实验上下文（P1-3 修复：采集任务一旦运行即写新记录）
    ok = await state.start(
        sample_id=sample_id,
        sensor_path_id=sensor_path_id,
        title=title,
        experiment_db_id=exp_id,
        experiment_uid=uid,
    )
    if not ok:
        # 极罕见竞态：另一请求已抢先进入 running；把刚建的空记录标记结束
        await persist.finish_experiment(exp_id, "idle")
        return ControlResponse(ok=False, status=state.status, message="实验已在进行中")

    await persist.upsert_sample(
        experiment_id=exp_id,
        sample_id=sample_id,
        sensor_path_id=sensor_path_id,
        concentration_mmol_l=body.concentration_mmol_l,
    )

    await broadcast({"status": "running"})
    return ControlResponse(
        ok=True,
        status="running",
        experiment_id=exp_id,
        sample_id=sample_id,
    )


@router.post("/api/experiment/stop")
async def stop() -> ControlResponse:
    changed = await state.stop()
    exp_id = state.experiment_db_id
    if changed and exp_id is not None:
        # 仅在 running→stopped 时结束实验：重复 stop 不再刷新 ended_at_utc
        await persist.flush()  # 确保在途帧先落库
        await persist.finish_experiment(exp_id, "stopped")
        await broadcast({"status": state.status})
    return ControlResponse(
        ok=True,
        status=state.status,
        experiment_id=exp_id,
        message=None if changed else "当前没有运行中的实验",
    )


@router.post("/api/experiment/reset")
async def reset() -> ControlResponse:
    exp_id = state.experiment_db_id
    if exp_id is not None:
        await persist.flush()
        if state.status == "running":
            # 运行中被打断 → aborted（与 SRS 状态机语义一致），而非 idle
            await persist.finish_experiment(exp_id, "aborted")
    await state.reset()
    await broadcast({"status": "idle"})
    return ControlResponse(ok=True, status="idle")


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
async def experiment_frames(exp_id: int, limit: int = 1000, offset: int = 0) -> dict:
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
    exp["frames"] = await asyncio.to_thread(storage.get_frames, exp_id, limit=1_000_000)
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


@router.post("/api/debug/bad-frame")
async def inject_bad_frame() -> dict:
    """推送一条非法帧（ec 为非数值），验证前端容错不崩溃（F10）。"""
    await broadcast({"timestamp": 1.0, "ec": "abc", "temperature": 25.0, "status": "running"})
    return {"ok": True, "injected": "bad-frame"}


@router.post("/api/debug/close-connections")
async def close_connections() -> dict:
    """强制关闭所有 WS 连接，验证前端断线检测与重连（F08/F09）。"""
    count = await _hub.close_all(code=1001, reason="debug close")
    return {"ok": True, "closed": count}


@router.post("/api/debug/burst")
async def burst(count: int = 10000) -> dict:
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
