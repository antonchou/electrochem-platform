"""FastAPI 应用入口：溶液导电性相对比较 · 模拟数据源。"""

import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .acquisition import acquisition
from .broadcast import hub
from .persistence import persist
from .routes import router
from .state import state

# 模块加载时一次性初始化根日志（避免重复配置）；供采集循环等打点使用
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 SQLite、启动后台落库任务与单一采集任务；关闭时反向停止。"""
    # app 是模块级单例，测试与嵌入式重载可能多次进入 lifespan；每次都从干净内存态开始。
    await state.reset()
    await persist.start()
    try:
        await acquisition.start()
        yield
    finally:
        active_exp_id = state.experiment_db_id
        was_running = state.status == "running"
        if was_running:
            await state.stop()
        await acquisition.stop()
        await hub.close_all(code=1001, reason="server shutdown")
        try:
            if was_running and active_exp_id is not None:
                try:
                    await persist.flush()
                except Exception:
                    logging.getLogger("app.main").exception("lifespan flush failed")
                try:
                    await persist.finish_experiment(active_exp_id, "aborted")
                except Exception:
                    logging.getLogger("app.main").exception("lifespan finish_experiment failed")
        finally:
            await persist.stop()
            await state.reset()


app = FastAPI(
    title="溶液导电性相对比较 · 模拟数据源",
    description="供前端联调与演示；真实后端接入后仅需更换前端连接地址。",
    # 版本单一来源：backend/app/__init__.py（T-22）
    version=__version__,
    lifespan=lifespan,
)

cors_origins = [
    origin.strip()
    for origin in os.environ.get("EC_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
cors_origin_regex = os.environ.get(
    "EC_CORS_ORIGIN_REGEX",
    # 八位组限定 0-255（防 10.999.999.999 之类非法 IP 误匹配）；内网段/localhost/*.local
    # 为教室局域网演示的既定策略，生产绑定范围由 EC_BIND 控制（默认仅本机）。
    r"^https?://(?:localhost|127\.0\.0\.1|10(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}"
    r"|192\.168(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){2}"
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){2}"
    r"|[a-zA-Z0-9-]+\.local)(?::\d+)?$",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=cors_origin_regex or None,
    allow_methods=["*"],
    allow_headers=["*"],
)

_cors_origin_pattern = re.compile(cors_origin_regex) if cors_origin_regex else None


def write_origin_allowed(origin: str | None, host: str | None) -> bool:
    """写请求的 Origin 是否可信：同源，或在 CORS 放行范围内。没有 Origin（curl、脚本）放行。

    CORS 只拦浏览器读响应，不拦「简单请求」本身：stop/reset 与不带请求体的 start 都是简单请求，
    任意网页都能借访问者的浏览器把它们发出去并被执行。所以写请求要在服务端按 Origin 再拦一次。
    """
    if not origin:
        return True
    if urlsplit(origin).netloc == host:  # 同源：生产由后端托管 dist
        return True
    if origin in cors_origins:
        return True
    return bool(_cors_origin_pattern and _cors_origin_pattern.fullmatch(origin))


@app.middleware("http")
async def reject_cross_site_writes(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and not write_origin_allowed(
        request.headers.get("origin"), request.headers.get("host")
    ):
        return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
    return await call_next(request)

app.include_router(router)


def _frontend_dist() -> Path | None:
    """生产托管目录：默认仓库 `frontend/dist`，可用 EC_FRONTEND_DIST 覆盖。"""
    override = os.environ.get("EC_FRONTEND_DIST", "").strip()
    path = Path(override) if override else Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    return path if path.is_dir() and (path / "index.html").is_file() else None


_dist = _frontend_dist()
if _dist is not None:
    # 必须挂在 API/WS 路由之后，避免吃掉 /api /ws /health。
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
    logging.getLogger("app.main").info("serving frontend from %s", _dist)
else:
    logging.getLogger("app.main").info(
        "frontend dist not found; API-only mode (dev: run Vite on :5173)"
    )
