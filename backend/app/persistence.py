"""持久化服务：后台异步落库，不阻塞 WebSocket 数据流。

- 帧写入走 asyncio.Queue + to_thread，攒批（≤200 条或 0.5s）批量 INSERT
- 实验/样品生命周期的少量写操作直接 to_thread 同步等结果
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from . import storage


class PersistService:
    def __init__(self) -> None:
        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        # 队列在应用事件循环内创建，确保与调用方同 loop（避免测试/多实例下"不同事件循环"错误）
        self._queue = asyncio.Queue()
        await asyncio.to_thread(storage.init_db)
        if self._task is None:
            self._task = asyncio.create_task(self._drain())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # 停止前把队列清空写入
        await self.flush()

    async def flush(self) -> None:
        if self._queue is None:
            return
        batch: List[Dict[str, Any]] = []
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch:
            await asyncio.to_thread(storage.insert_frames, batch)

    def enqueue_frame(self, frame: Dict[str, Any]) -> None:
        if self._queue is not None:
            self._queue.put_nowait(frame)

    async def _drain(self) -> None:
        batch: List[Dict[str, Any]] = []
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if batch:
                    await asyncio.to_thread(storage.insert_frames, batch)
                    batch.clear()
                continue
            batch.append(item)
            # 攒批：最多 200 条，或队空时立即写入，避免数据滞留内存
            while len(batch) < 200 and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            if self._queue.empty() or len(batch) >= 200:
                await asyncio.to_thread(storage.insert_frames, batch)
                batch.clear()

    # ---------- 实验生命周期（少量写，直接等待） ----------

    async def create_experiment(self, **kwargs: Any) -> int:
        return await asyncio.to_thread(storage.create_experiment, **kwargs)

    async def finish_experiment(self, experiment_id: int, status: str = "stopped") -> None:
        await asyncio.to_thread(storage.finish_experiment, experiment_id, status)

    async def upsert_sample(self, **kwargs: Any) -> None:
        await asyncio.to_thread(storage.upsert_sample, **kwargs)


persist = PersistService()
