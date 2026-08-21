"""FastAPI 应用入口：溶液导电性相对比较 · 模拟数据源。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .persistence import persist
from .routes import router, start_acquisition, stop_acquisition

# 模块加载时一次性初始化根日志（避免重复配置）；供 routes 采集循环等打点使用
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 SQLite、启动后台落库任务与单一采集任务；关闭时反向停止。"""
    await persist.start()
    await start_acquisition()
    yield
    await stop_acquisition()
    await persist.stop()


app = FastAPI(
    title="溶液导电性相对比较 · 模拟数据源",
    description="供前端联调与演示；真实后端接入后仅需更换前端连接地址。",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 仅开发/演示用途
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
