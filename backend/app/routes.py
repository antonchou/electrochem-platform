"""路由：WebSocket 实时流 + REST 控制 + 历史查询与导出 + 备选公式拟合。

采集任务本身（驱动、节拍、组帧、续跑去重、落库告警）在 acquisition.py；
这里只做 HTTP/WS 层与实验生命周期编排。
"""

import asyncio
import datetime
import json
import logging
import math
import os
import random
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect

from . import analysis, stability, storage
from .acquisition import FRAME_META_FIELDS, acquisition
from .broadcast import hub
from .persistence import PERSIST_DEGRADED_MESSAGE, persist
from .schemas import (
    CalibrationRequest,
    ControlResponse,
    CurrentExperimentResponse,
    ExperimentStartRequest,
    FitRequest,
)
from .state import DEFAULT_SAMPLE_ID, DEFAULT_SENSOR_PATH_ID, state

router = APIRouter()

logger = logging.getLogger("app.routes")

# 实验生命周期（start/stop/reset）串行化：并发请求不得交错改写内存态与数据库
_lifecycle_lock = asyncio.Lock()


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
    await hub.connect(ws)
    try:
        # 晚连/刷新：一次性告警可能已发出且当时无订阅者，连接时补发。
        if persist.degraded:
            await hub.send_to(ws, acquisition.persist_degraded_payload())
        # 客户端不发送业务消息，这里阻塞等待断连信号
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await hub.disconnect(ws)


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
            if reopened:
                # 去重基准必须在进入 running 之前装好（R3-5）：state.resume() 之后采集循环随时
                # 可能产出续跑首帧，基准晚到会让首帧漏检、一次性窗口错落到第二帧上。
                await acquisition.load_resume_boundary(exp_id)
                if await state.resume():
                    acquisition.reset_notices()
                    await hub.publish(
                        {"status": "running", "experiment_id": exp_id, "sample_id": state.sample_id}
                    )
                    if persist.degraded:
                        await acquisition.notify_persist_degraded()
                    return ControlResponse(
                        ok=True,
                        status="running",
                        experiment_id=exp_id,
                        sample_id=state.sample_id,
                        resumed=True,
                        persistence="degraded" if persist.degraded else None,
                        message=PERSIST_DEGRADED_MESSAGE if persist.degraded else None,
                    )
                acquisition.clear_resume_boundary()

        sample_id = body.sample_id or DEFAULT_SAMPLE_ID
        sensor_path_id = body.sensor_path_id or DEFAULT_SENSOR_PATH_ID
        title = body.title or "不同溶液导电性相对比较"
        uid = f"EXP-{datetime.datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:4]}"
        params = acquisition.measurement_params()
        env_cal = os.environ.get("EC_CALIBRATION_ID", "").strip()
        cal_id = env_cal or params["driver_calibration_id"]

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
                claimed = params["calibration_claimed"]
                await persist.insert_calibration_record(
                    experiment_id=exp_id,
                    calibration_id=cal_id,
                    sensor_path_id=sensor_path_id,
                    mode=params["calibration_mode"],
                    standard=params["calibration_standard"],
                    lot=params["calibration_lot"],
                    coeff_value=params["cell_constant_per_cm"] if claimed else None,
                    coeff_json={
                        "cell_constant_per_cm": params["cell_constant_per_cm"],
                        "alpha_per_c": params["alpha_per_c"],
                        **{name: params[name] for name in FRAME_META_FIELDS},
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

        acquisition.reset_session()
        # 带上样品号：旁观端（其它浏览器）据此更新溶液名，不再显示自己输入框里的旧值
        await hub.publish({"status": "running", "experiment_id": exp_id, "sample_id": sample_id})
        if persist.degraded:
            await acquisition.notify_persist_degraded()
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
            payload: dict = {"status": status, "experiment_id": exp_id, "sample_id": state.sample_id}
            if not persist_ok:
                payload["message"] = PERSIST_DEGRADED_MESSAGE
                payload["persistence"] = "degraded"
            await hub.publish(payload)
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
    判稳看窗口覆盖的全部原始帧（含 COMPUTE_INVALID 帧上的硬标志），见 stability.qc_from_frames。
    """
    try:
        rows = await asyncio.to_thread(storage.get_recent_frames, exp_id, limit=500)
        result = stability.qc_from_frames(rows)
        if result is None:
            return
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
        acquisition.reset_session()
        payload: dict = {"status": "idle"}
        message = None
        if not persist_ok:
            payload["message"] = PERSIST_DEGRADED_MESSAGE
            payload["persistence"] = "degraded"
            message = PERSIST_DEGRADED_MESSAGE
        await hub.publish(payload)
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
    mode: Literal["head", "tail", "even"] = "head",
) -> Response:
    """原始帧查询，返回 {frames, total, mode}（R3-6）。

    - head（默认）：按落库顺序从 offset 起取 limit 条
    - tail：最新 limit 条（仍按时间正序）——续跑水合只需要尾部，不必拉全量前缀
    - even：全实验等间隔抽样至多 limit 条（保留首末帧）——历史详情的曲线/拟合覆盖整条实验

    取数与编码都在工作线程，且逐帧编码：10 万帧约 63MB，旧实现交给 FastAPI 在事件循环上
    序列化，实测让运行中实验的采集停顿约 0.5s。整包 json.dumps 是一次持有 GIL 的 C 调用，
    放进线程也会卡住事件循环；逐帧编码之间有字节码，GIL 能按 5ms 切换间隔让回事件循环。
    """
    if mode != "head" and offset:
        raise HTTPException(status_code=400, detail="offset 只能与 mode=head 一起使用")

    def query() -> str:
        if mode == "tail":
            rows = storage.get_recent_frames(exp_id, limit=limit)
        elif mode == "even":
            rows = storage.get_frames_even(exp_id, max_points=limit)
        else:
            rows = storage.get_frames(exp_id, limit=limit, offset=offset)
        total = storage.count_frames(exp_id)
        # 与 FastAPI JSONResponse 同口径：紧凑分隔符、拒绝 NaN
        frames = ",".join(
            json.dumps(r, ensure_ascii=False, allow_nan=False, separators=(",", ":")) for r in rows
        )
        return f'{{"frames":[{frames}],"total":{total},"mode":"{mode}"}}'

    return Response(content=await asyncio.to_thread(query), media_type="application/json")


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
    """导出完整实验（元信息 + 全部帧）为 JSON。

    取数与 json.dumps 都在工作线程完成，避免长实验序列化阻塞事件循环（T-07）。
    """
    try:
        payload = await asyncio.to_thread(storage.export_json, exp_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="experiment not found")
    return Response(
        content=payload,
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
        if results:
            # 空结果不落库（P1-4）：insert_fit_results 会先 DELETE 同轴既有记录，
            # 空列表 + 落库 = 用一次失败拟合清空历史结果；derived 报告同理不覆写。
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


@router.post("/api/analysis/calibration")
async def calibration(body: CalibrationRequest) -> dict:
    """跨实验浓度标定：每个实验取一个点（κ25 代表值 vs 浓度），按浓度轴模型池拟合。

    单个实验只有一种浓度，浓度轴拟合只能跨实验做（09-30 审查 #4）。取点在服务端从库里
    读（不信任前端传值），结果报告写 data/derived/calibration_*.json，内含成员实验以便溯源。
    """
    ids = list(dict.fromkeys(body.experiment_ids))
    points, problems = await asyncio.to_thread(storage.calibration_points, ids)
    if problems:
        raise HTTPException(status_code=400, detail="；".join(problems))
    if len({p["concentration_mmol_l"] for p in points}) < 3:
        raise HTTPException(status_code=400, detail="跨实验标定至少需要 3 个不同浓度")
    x = [p["concentration_mmol_l"] for p in points]
    y = [p["kappa25_us_cm"] for p in points]
    results = await asyncio.to_thread(analysis.fit_all, x, y, body.models, "concentration")
    best = results[0]["model"] if results else None
    derived_path = None
    if results:
        payload = {
            "kind": "calibration",
            "x_axis": "concentration",
            "created_at_utc": storage.utc_now(),
            "experiment_ids": ids,
            "points": points,
            "best": best,
            "models": results,
        }
        derived_path = await asyncio.to_thread(storage.write_calibration_report, ids, payload)
    return {"best": best, "models": results, "points": points, "derived_path": derived_path}


# ---------- 调试接口（仅模拟源使用；用于验收 F08/F09/F10/P04） ----------


def _require_debug_enabled() -> None:
    enabled = os.environ.get("EC_ENABLE_DEBUG_ENDPOINTS", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        # 对生产调用方隐藏调试面；测试/演示须显式启用。
        raise HTTPException(status_code=404, detail="not found")


@router.post("/api/debug/bad-frame", dependencies=[Depends(_require_debug_enabled)])
async def inject_bad_frame() -> dict:
    """推送一条非法帧（ec 为非数值），验证前端容错不崩溃（F10）。"""
    await hub.publish({"timestamp": 1.0, "ec": "abc", "temperature": 25.0, "status": "running"})
    return {"ok": True, "injected": "bad-frame"}


@router.post("/api/debug/close-connections", dependencies=[Depends(_require_debug_enabled)])
async def close_connections() -> dict:
    """强制关闭所有 WS 连接，验证前端断线检测与重连（F08/F09）。"""
    count = await hub.close_all(code=1001, reason="debug close")
    return {"ok": True, "closed": count}


def _burst_frame(t: float) -> dict:
    """V1 简化帧：κ25 ≈ 1413 μS/cm + 缓慢漂移 + 噪声（无 U/I、无 experiment_id）。"""
    return {
        "timestamp": round(t, 2),
        "ec": round(1413.0 + math.sin(t / 30.0) * 6.0 + (random.random() - 0.5) * 3.0, 1),
        "temperature": round(25.0 + (random.random() - 0.5) * 0.3, 2),
        "status": "running",
    }


@router.post("/api/debug/burst", dependencies=[Depends(_require_debug_enabled)])
async def burst(count: Annotated[int, Query(ge=1, le=10_000)] = 10_000) -> dict:
    """快速推送 count 帧（默认 1 万），用于验证前端大点数负载与 30 分钟模拟（P03/P04）。

    仅广播、不落库、不消耗 seq：注入帧不进入 raw_frames（B-3 修复，保证原始数据纯净）。

    每 200 帧以 sleep(0) 让出事件循环（T-08）：broadcast 对同一 payload 只序列化一次，
    但 1 万次造帧 + 序列化 + 入队若不让出，会拖慢采集周期使负载测试失真。
    """
    sent = 0
    for i in range(count):
        sent += await hub.publish(_burst_frame(i * 0.1))
        if i % 200 == 199:
            await asyncio.sleep(0)
    return {"ok": True, "sent": sent}
