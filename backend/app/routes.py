"""路由：WebSocket 实时流 + REST 控制 + 历史查询与导出 + 备选公式拟合。"""

import asyncio
import datetime
import json
import math
import time
import uuid

from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect

from . import analysis, storage
from .persistence import persist
from .schemas import ControlResponse, ExperimentStartRequest, FitRequest
from .state import state
from .stream import generate_frame

router = APIRouter()

# 活跃的 WebSocket 连接集合（用于状态广播）
_connections: set[WebSocket] = set()


async def broadcast(payload: dict) -> None:
    """向所有活跃连接推送一条消息；连接异常则移除。"""
    text = json.dumps(payload, ensure_ascii=False)
    for ws in list(_connections):
        try:
            await ws.send_text(text)
        except Exception:
            _connections.discard(ws)


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _frame_to_row(t_seconds: float, ec: float, temperature: float) -> dict:
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
        "quality_flags": None,
        "status": state.status,
    }


def _enqueue_frame(t_seconds: float, ec: float, temperature: float) -> None:
    """后台异步落库（仅当前有实验上下文时）。"""
    if state.experiment_db_id is not None:
        persist.enqueue_frame(_frame_to_row(t_seconds, ec, temperature))


@router.websocket("/ws/stream")
async def ws_stream(ws: WebSocket) -> None:
    """实时数据流：实验 running 期间以 10 Hz 推送数据帧，并异步落库。"""
    await ws.accept()
    _connections.add(ws)
    try:
        while True:
            if state.status == "running":
                frame = generate_frame(state.elapsed())
                # 先落库再推送：客户端收到帧时，该帧必然已进入持久化队列（避免停止时丢帧）
                _enqueue_frame(frame["timestamp"], frame["ec"], frame["temperature"])
                await ws.send_text(json.dumps(frame, ensure_ascii=False))
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(ws)


@router.post("/api/experiment/start")
async def start(body: ExperimentStartRequest | None = None) -> ControlResponse:
    body = body or ExperimentStartRequest()
    ok = await state.start(
        sample_id=body.sample_id or "SAMPLE",
        sensor_path_id=body.sensor_path_id or "CM2_WIDE",
        title=body.title or "不同溶液导电性相对比较",
    )
    if not ok:
        return ControlResponse(ok=False, status=state.status, message="实验已在进行中")

    # Phase 7：新建实验记录与样品记录（溯源）
    uid = f"EXP-{datetime.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:4]}"
    exp_id = await persist.create_experiment(
        experiment_id=uid,
        title=body.title or "不同溶液导电性相对比较",
        operator=body.operator,
        objective=body.objective,
        sample_id=state.sample_id,
        sensor_path_id=state.sensor_path_id,
        metadata={
            "sample_id": state.sample_id,
            "sensor_path_id": state.sensor_path_id,
            "concentration_mmol_l": body.concentration_mmol_l,
        },
    )
    state.experiment_db_id = exp_id
    state.experiment_uid = uid
    await persist.upsert_sample(
        experiment_id=exp_id,
        sample_id=state.sample_id,
        sensor_path_id=state.sensor_path_id,
        concentration_mmol_l=body.concentration_mmol_l,
    )

    await broadcast({"status": "running"})
    return ControlResponse(
        ok=True,
        status="running",
        experiment_id=exp_id,
        sample_id=state.sample_id,
    )


@router.post("/api/experiment/stop")
async def stop() -> ControlResponse:
    changed = await state.stop()
    exp_id = state.experiment_db_id
    if exp_id is not None:
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
            await persist.finish_experiment(exp_id, "idle")
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
    count = len(_connections)
    for ws in list(_connections):
        try:
            await ws.close(code=1001, reason="debug close")
        except Exception:
            pass
    return {"ok": True, "closed": count}


@router.post("/api/debug/burst")
async def burst(count: int = 10000) -> dict:
    """快速推送 count 帧（默认 1 万），用于验证前端大点数负载与 30 分钟模拟（P03/P04）。"""
    sent = 0
    for i in range(count):
        frame = generate_frame(i * 0.1)
        text = json.dumps(frame, ensure_ascii=False)
        for ws in list(_connections):
            try:
                await ws.send_text(text)
                sent += 1
            except Exception:
                _connections.discard(ws)
        _enqueue_frame(frame["timestamp"], frame["ec"], frame["temperature"])
        if i % 200 == 199:
            await asyncio.sleep(0.002)
    return {"ok": True, "sent": sent}
