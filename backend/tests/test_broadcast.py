"""WebSocket fan-out must isolate slow subscribers."""

import asyncio
import json

from app.broadcast import BroadcastHub


class _SlowWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.closed = False
        self.gate = asyncio.Event()
        self.sent: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, text: str) -> None:
        await self.gate.wait()
        self.sent.append(text)

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = True


class _FailingWebSocket(_SlowWebSocket):
    async def send_text(self, text: str) -> None:
        raise RuntimeError("send failed")


def test_slow_subscriber_drops_oldest_without_blocking_publisher():
    async def scenario() -> None:
        hub = BroadcastHub(queue_size=2)
        websocket = _SlowWebSocket()
        await hub.connect(websocket)  # type: ignore[arg-type]

        assert await hub.publish({"seq": 0}) == 1
        await asyncio.sleep(0)  # sender takes seq=0, then blocks in send_text
        assert await hub.publish({"seq": 1}) == 1
        assert await hub.publish({"seq": 2}) == 1
        assert await hub.publish({"seq": 3}) == 1

        assert hub.stats.subscriber_count == 1
        assert hub.stats.queued_messages == 2
        assert hub.stats.dropped_messages == 1

        websocket.gate.set()
        for _ in range(20):
            if len(websocket.sent) == 3:
                break
            await asyncio.sleep(0)
        assert [json.loads(item)["seq"] for item in websocket.sent] == [0, 2, 3]

        assert await hub.close_all(reason="test complete") == 1
        assert websocket.closed is True
        assert hub.stats.subscriber_count == 0

    asyncio.run(scenario())


def test_sender_failure_closes_and_removes_subscriber():
    async def scenario() -> None:
        hub = BroadcastHub(queue_size=2)
        websocket = _FailingWebSocket()
        await hub.connect(websocket)  # type: ignore[arg-type]
        await hub.publish({"seq": 1})
        for _ in range(20):
            if hub.stats.subscriber_count == 0:
                break
            await asyncio.sleep(0)
        assert websocket.closed is True
        assert hub.stats.subscriber_count == 0

    asyncio.run(scenario())
