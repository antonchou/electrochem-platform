"""WebSocket 广播：每个客户端一个有界队列，慢客户端不会拖住采集。

队列满时不是悄悄丢掉最旧的消息（那样可能丢掉「测量开始」这类状态消息，前端就对不上了），
而是清空该客户端的队列，换成一份最新快照：客户端收到快照整体重建，状态总是一致的。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable


def encode(message: dict[str, Any]) -> str:
    return json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class Hub:
    def __init__(self, snapshot: Callable[[], dict[str, Any]], queue_size: int = 1000) -> None:
        self._snapshot = snapshot
        self._queue_size = queue_size
        self._queues: set[asyncio.Queue[str]] = set()

    @property
    def client_count(self) -> int:
        return len(self._queues)

    def subscribe(self) -> asyncio.Queue[str]:
        """新客户端：队列里第一条就是快照，之后是增量。两步之间没有 await，不会漏消息。"""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_size)
        queue.put_nowait(encode(self._snapshot()))
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._queues.discard(queue)

    def publish(self, message: dict[str, Any]) -> None:
        text = encode(message)
        resync: str | None = None
        for queue in self._queues:
            try:
                queue.put_nowait(text)
            except asyncio.QueueFull:
                # 快照已包含这条消息的效果（调用方先改状态再发布），所以只放快照
                while not queue.empty():
                    queue.get_nowait()
                resync = resync or encode(self._snapshot())
                queue.put_nowait(resync)
