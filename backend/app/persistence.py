"""持久化服务：后台异步落库，不阻塞 WebSocket 数据流。

- 帧写入走 asyncio.Queue + to_thread，攒批（≤200 条或 0.5s）批量 INSERT
- 实验/样品生命周期的少量写操作直接 to_thread 同步等结果
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import storage


@dataclass(slots=True)
class _FlushBarrier:
    completed: asyncio.Future[None]


_STOP = object()


class PersistService:
    def __init__(self) -> None:
        self._queue: Optional[asyncio.Queue[object]] = None
        self._task: Optional[asyncio.Task] = None
        self._accepting = False

    async def start(self) -> None:
        # 队列在应用事件循环内创建，确保与调用方同 loop（避免测试/多实例下"不同事件循环"错误）
        await asyncio.to_thread(storage.init_db)
        if self._task is not None and not self._task.done():
            return
        self._queue = asyncio.Queue()
        self._accepting = True
        self._task = asyncio.create_task(self._drain(), name="sqlite-frame-writer")

    async def stop(self) -> None:
        """有序停止写入器；返回时队列及已提交的线程写入都已经完成。"""
        self._accepting = False
        task = self._task
        queue = self._queue
        if task is None:
            return
        if not task.done() and queue is not None:
            queue.put_nowait(_STOP)
        try:
            await asyncio.shield(task)
        finally:
            self._task = None
            self._queue = None

    async def flush(self) -> None:
        """等待调用前已入队的所有帧真正提交到 SQLite。"""
        queue = self._queue
        task = self._task
        if queue is None or task is None:
            return
        if task.done():
            await task
            return
        completed = asyncio.get_running_loop().create_future()
        queue.put_nowait(_FlushBarrier(completed))
        done, _ = await asyncio.wait(
            {completed, task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if task in done and not completed.done():
            await task
        await completed

    def enqueue_frame(self, frame: Dict[str, Any]) -> None:
        if self._accepting and self._queue is not None:
            self._queue.put_nowait(frame)

    async def _drain(self) -> None:
        batch: List[Dict[str, Any]] = []
        deadline: float | None = None
        while True:
            timeout = 0.5 if deadline is None else max(0.0, deadline - asyncio.get_running_loop().time())
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                if batch:
                    await asyncio.to_thread(storage.insert_frames, batch)
                    batch.clear()
                deadline = None
                continue

            if item is _STOP:
                if batch:
                    await asyncio.to_thread(storage.insert_frames, batch)
                return
            if isinstance(item, _FlushBarrier):
                try:
                    if batch:
                        await asyncio.to_thread(storage.insert_frames, batch)
                        batch.clear()
                    item.completed.set_result(None)
                except Exception as exc:
                    item.completed.set_exception(exc)
                    raise
                deadline = None
                continue

            if not isinstance(item, dict):
                raise TypeError(f"unsupported persistence queue item: {type(item)!r}")
            batch.append(item)
            if deadline is None:
                deadline = asyncio.get_running_loop().time() + 0.5
            if len(batch) >= 200:
                await asyncio.to_thread(storage.insert_frames, batch)
                batch.clear()
                deadline = None

    # ---------- 实验生命周期（少量写，直接等待） ----------

    async def create_experiment(self, **kwargs: Any) -> int:
        return await asyncio.to_thread(storage.create_experiment, **kwargs)

    async def create_experiment_with_sample(self, **kwargs: Any) -> int:
        return await asyncio.to_thread(storage.create_experiment_with_sample, **kwargs)

    async def finish_experiment(self, experiment_id: int, status: str = "stopped") -> None:
        await asyncio.to_thread(storage.finish_experiment, experiment_id, status)

    async def upsert_sample(self, **kwargs: Any) -> None:
        await asyncio.to_thread(storage.upsert_sample, **kwargs)

    async def update_sample_qc(self, **kwargs: Any) -> None:
        await asyncio.to_thread(storage.update_sample_qc, **kwargs)


persist = PersistService()
