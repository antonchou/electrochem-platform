"""HTTP / WebSocket 接口与前端静态文件托管。接口层只做参数校验与错误映射，业务在 lab / records。

前端与接口同源（生产由本进程托管 dist，开发经 Vite 代理），所以不开 CORS；
浏览器发来的写请求和 WS 连接如果带着别的站点的 Origin，一律拒绝，防止被其他网页跨站操作。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Mapping
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import records
from .chemistry import STANDARDS
from .devices import FAULTS, Device, SimulatedCell, make_device
from .errors import Conflict, Invalid, NotFound, Unavailable
from .lab import Lab
from .settings import Settings
from .store import Store

HEARTBEAT_S = 5.0
_HEARTBEAT = '{"type":"heartbeat"}'
_ERROR_STATUS = {NotFound: 404, Conflict: 409, Invalid: 422, Unavailable: 503}


class _Body(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class StartBody(_Body):
    sample_name: str = Field(min_length=1, max_length=100)
    concentration_mmol_l: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=500)


class CalibrationPointBody(_Body):
    measurement_id: int
    standard_name: str = Field(min_length=1, max_length=100)
    standard_kappa25_us_cm: float = Field(gt=0, allow_inf_nan=False)


class CalibrationBody(_Body):
    points: list[CalibrationPointBody] = Field(min_length=1, max_length=20)
    operator: str | None = Field(default=None, max_length=100)
    cell_id: str | None = Field(default=None, max_length=100)
    lot: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)


class ConcentrationBody(_Body):
    measurement_ids: list[int] = Field(min_length=2, max_length=200)


class SimulatorBody(_Body):
    fault: str | None = None
    kappa25_us_cm: float | None = Field(default=None, ge=0, allow_inf_nan=False)


def same_origin(headers: Mapping[str, str]) -> bool:
    """没有 Origin（curl、脚本）放行；有 Origin 时其主机端口必须与 Host 一致。"""
    origin = headers.get("origin")
    if not origin:
        return True
    return urlsplit(origin).netloc == headers.get("host")


def create_app(settings: Settings | None = None, device: Device | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = await asyncio.to_thread(Store, settings.db_path)
        lab = Lab(settings, store, device or make_device(settings))
        await lab.start()
        app.state.lab = lab
        try:
            yield
        finally:
            await lab.close()

    app = FastAPI(title="电导率实验平台 v2", lifespan=lifespan)

    @app.middleware("http")
    async def reject_cross_site_writes(request: Request, call_next: Any) -> Any:
        if request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin(request.headers):
            return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # 默认响应会回显输入值；输入里有 NaN 时回显本身无法编码成 JSON，422 会变成 500
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    for error_type, status in _ERROR_STATUS.items():
        app.add_exception_handler(
            error_type,
            lambda _request, exc, status=status: JSONResponse({"detail": str(exc)}, status_code=status),
        )

    def lab_of(request: Request) -> Lab:
        return request.app.state.lab

    # ---- 状态与控制 ----
    @app.get("/api/state")
    async def get_state(request: Request) -> dict[str, Any]:
        return lab_of(request).state()

    @app.get("/api/standards")
    async def get_standards() -> list[dict[str, Any]]:
        return [{"name": s.name, "kappa25_us_cm": s.kappa25_us_cm} for s in STANDARDS]

    @app.post("/api/measurements", status_code=201)
    async def start_measurement(body: StartBody, request: Request) -> dict[str, Any]:
        return await lab_of(request).start_measurement(body.sample_name, body.concentration_mmol_l, body.note or None)

    @app.post("/api/measurements/current/stop")
    async def stop_measurement(request: Request) -> dict[str, Any]:
        return await lab_of(request).stop_measurement()

    # ---- 历史 ----
    @app.get("/api/measurements")
    async def list_measurements(
        request: Request, limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(lab_of(request).store.list_measurements, limit, offset)

    @app.get("/api/measurements/{measurement_id}")
    async def get_measurement(measurement_id: int, request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(records.require_measurement, lab_of(request).store, measurement_id)

    @app.get("/api/measurements/{measurement_id}/points")
    async def get_points(
        measurement_id: int, request: Request, max_points: int = Query(4000, ge=10, le=50_000)
    ) -> dict[str, Any]:
        return await asyncio.to_thread(records.measurement_points, lab_of(request).store, measurement_id, max_points)

    @app.get("/api/measurements/{measurement_id}/frames.csv")
    async def export_csv(measurement_id: int, request: Request) -> Response:
        filename, text = await asyncio.to_thread(records.export_csv, lab_of(request).store, measurement_id)
        return Response(
            text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/measurements/{measurement_id}/temperature-fit")
    async def temperature_fit(measurement_id: int, request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(records.temperature_analysis, lab_of(request).store, measurement_id)

    @app.post("/api/analysis/concentration")
    async def concentration(body: ConcentrationBody, request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(records.concentration_analysis, lab_of(request).store, body.measurement_ids)

    # ---- 标定 ----
    @app.get("/api/calibrations")
    async def list_calibrations(request: Request) -> list[dict[str, Any]]:
        return await asyncio.to_thread(lab_of(request).store.list_calibrations)

    @app.post("/api/calibrations", status_code=201)
    async def calibrate(body: CalibrationBody, request: Request) -> dict[str, Any]:
        meta = body.model_dump(exclude={"points"})
        return await lab_of(request).calibrate([p.model_dump() for p in body.points], meta)

    # ---- 模拟器（演示与测试用） ----
    @app.post("/api/simulator")
    async def control_simulator(body: SimulatorBody, request: Request) -> dict[str, Any]:
        simulated = lab_of(request).device
        if not isinstance(simulated, SimulatedCell):
            raise Conflict("当前设备不是模拟器")
        if body.fault is not None:
            if body.fault not in FAULTS:
                raise Invalid(f"未知故障 {body.fault!r}，可选：{', '.join(FAULTS)}")
            simulated.set_fault(body.fault)
        if body.kappa25_us_cm is not None:
            simulated.set_solution(body.kappa25_us_cm)
        return {"fault": simulated.fault}

    # ---- 实时推送 ----
    @app.websocket("/ws")
    async def live(ws: WebSocket) -> None:
        if not same_origin(ws.headers):
            await ws.close(code=1008)
            return
        await ws.accept()
        hub = ws.app.state.lab.hub
        queue = hub.subscribe()

        async def send() -> None:
            while True:
                try:
                    text = await asyncio.wait_for(queue.get(), HEARTBEAT_S)
                except TimeoutError:
                    text = _HEARTBEAT
                await ws.send_text(text)

        async def receive() -> None:
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass

        tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            hub.unsubscribe(queue)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    if (settings.static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")
    return app
