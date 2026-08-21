"""FastAPI 应用入口：溶液导电性相对比较 · 模拟数据源。"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .persistence import persist
from .routes import router, start_acquisition, stop_acquisition
from .state import state

# 模块加载时一次性初始化根日志（避免重复配置）；供 routes 采集循环等打点使用
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 SQLite、启动后台落库任务与单一采集任务；关闭时反向停止。"""
    # app 是模块级单例，测试与嵌入式重载可能多次进入 lifespan；每次都从干净内存态开始。
    await state.reset()
    await persist.start()
    try:
        await start_acquisition()
        yield
    finally:
        active_exp_id = state.experiment_db_id
        was_running = state.status == "running"
        if was_running:
            await state.stop()
        await stop_acquisition()
        if was_running and active_exp_id is not None:
            await persist.flush()
            await persist.finish_experiment(active_exp_id, "aborted")
        await persist.stop()
        await state.reset()


app = FastAPI(
    title="溶液导电性相对比较 · 模拟数据源",
    description="供前端联调与演示；真实后端接入后仅需更换前端连接地址。",
    version="2.0.0",
    lifespan=lifespan,
)

cors_origins = [
    origin.strip()
    for origin in os.environ.get("EC_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
cors_origin_regex = os.environ.get(
    "EC_CORS_ORIGIN_REGEX",
    r"^https?://(?:localhost|127\.0\.0\.1|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|[a-zA-Z0-9-]+\.local)(?::\d+)?$",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=cors_origin_regex or None,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
