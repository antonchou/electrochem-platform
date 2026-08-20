"""FastAPI 应用入口：溶液导电性相对比较 · 模拟数据源。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .persistence import persist
from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 SQLite 并启动后台落库任务；关闭时清空队列。"""
    await persist.start()
    yield
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
