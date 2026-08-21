"""Non-blocking WebSocket fan-out with bounded per-client queues."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import dataclass

from fastapi import WebSocket


logger = logging.getLogger("app.broadcast")


@dataclass(frozen=True, slots=True)
class BroadcastStats:
    subscriber_count: int
    queued_messages: int
    dropped_messages: int


@dataclass(slots=True)
class _Subscriber:
    websocket: WebSocket
    queue: asyncio.Queue[str]
    sender_task: asyncio.Task[None] | None = None


class BroadcastHub:
    """Fan out messages without letting one slow client block acquisition.

    Each subscriber owns a finite queue and a dedicated sender task. When that
    queue is full, the oldest pending item is discarded so the client catches
    up to current state instead of applying unbounded backpressure to the
    acquisition loop.
    """

    def __init__(self, *, queue_size: int = 20_000) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self._queue_size = queue_size
        self._subscribers: dict[WebSocket, _Subscriber] = {}
        self._dropped_messages = 0

    @property
    def stats(self) -> BroadcastStats:
        return BroadcastStats(
            subscriber_count=len(self._subscribers),
            queued_messages=sum(item.queue.qsize() for item in self._subscribers.values()),
            dropped_messages=self._dropped_messages,
        )

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        old = self._subscribers.pop(websocket, None)
        if old is not None:
            await self._cancel_sender(old)

        subscriber = _Subscriber(
            websocket=websocket,
            queue=asyncio.Queue(maxsize=self._queue_size),
        )
        self._subscribers[websocket] = subscriber
        subscriber.sender_task = asyncio.create_task(
            self._sender(subscriber),
            name="websocket-broadcast-sender",
        )

    async def disconnect(self, websocket: WebSocket) -> None:
        subscriber = self._subscribers.pop(websocket, None)
        if subscriber is not None:
            await self._cancel_sender(subscriber)

    async def publish(self, payload: dict) -> int:
        """Queue one JSON payload for every connected subscriber.

        Returns the number of subscriber queues that accepted the payload. A
        full queue still accepts the new payload after dropping its oldest
        pending item.
        """
        text = json.dumps(payload, ensure_ascii=False)
        accepted = 0
        for websocket, subscriber in list(self._subscribers.items()):
            task = subscriber.sender_task
            if task is None or task.done():
                self._subscribers.pop(websocket, None)
                continue
            if subscriber.queue.full():
                with suppress(asyncio.QueueEmpty):
                    subscriber.queue.get_nowait()
                    self._dropped_messages += 1
            subscriber.queue.put_nowait(text)
            accepted += 1
        return accepted

    async def close_all(self, *, code: int = 1001, reason: str = "server close") -> int:
        subscribers = list(self._subscribers.values())
        self._subscribers.clear()
        for subscriber in subscribers:
            await self._cancel_sender(subscriber)
        if subscribers:
            await asyncio.gather(
                *(item.websocket.close(code=code, reason=reason) for item in subscribers),
                return_exceptions=True,
            )
        return len(subscribers)

    async def _sender(self, subscriber: _Subscriber) -> None:
        try:
            while True:
                text = await subscriber.queue.get()
                await subscriber.websocket.send_text(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("WebSocket sender stopped", exc_info=True)
            # 主动完成 ASGI WebSocket 关闭握手，避免仅从订阅表移除后遗留半开连接。
            with suppress(Exception):
                await subscriber.websocket.close(code=1011, reason="broadcast send failed")
        finally:
            current = self._subscribers.get(subscriber.websocket)
            if current is subscriber:
                self._subscribers.pop(subscriber.websocket, None)

    async def _cancel_sender(self, subscriber: _Subscriber) -> None:
        task = subscriber.sender_task
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
