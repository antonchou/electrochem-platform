"""后台落库服务 PersistService：停止/flush 竞态、写入失败降级，以及降级在接口层的可见性。"""

import asyncio
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.acquisition import acquisition
from app.main import app
from app.persistence import PersistService, _FlushBarrier, _STOP, persist


def test_flush_barrier_after_stop_marker_resolves(tmp_path, monkeypatch):
    """T-01：barrier 排在 _STOP 之后时，stop() 不得让 flush() 永久挂起。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t01.db"))

    async def scenario():
        service = PersistService()
        await service.start()
        # 复现竞态窗口：stop() 的 _STOP 先入队，随后并发调用的 flush() 才入队 barrier。
        # 修复前 drain 读到 _STOP 直接 return，barrier 永不 resolve。
        service._queue.put_nowait(_STOP)
        flush_task = asyncio.create_task(service.flush())
        await asyncio.sleep(0)  # 让 flush 先于 drain 执行、把 barrier 排进队列
        await asyncio.wait_for(service.stop(), timeout=1.0)
        await asyncio.wait_for(flush_task, timeout=1.0)

    asyncio.run(scenario())


def test_stop_resolves_barriers_enqueued_behind_stop_marker(tmp_path, monkeypatch):
    """T-01（验收口径）：barrier 在队列中位于 _STOP 之后，drain 退出前必须 settle。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t01b.db"))

    async def scenario():
        service = PersistService()
        await service.start()
        completed = asyncio.get_running_loop().create_future()
        service._queue.put_nowait(_STOP)
        service._queue.put_nowait(_FlushBarrier(completed))
        await asyncio.wait_for(service.stop(), timeout=1.0)
        assert completed.done()
        assert completed.exception() is None

    asyncio.run(scenario())


def test_stop_swallows_writer_exception(tmp_path, monkeypatch):
    """T-19：writer 带异常退出时 stop() 不向上抛，清理流程照常执行。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t19.db"))

    async def scenario():
        service = PersistService()
        await service.start()
        service._queue.put_nowait(object())  # 非法 item → writer 抛 TypeError 崩溃
        await asyncio.wait_for(service.stop(), timeout=1.0)  # 不得抛出
        assert service._task is None
        assert service._queue is None

    asyncio.run(scenario())


def test_persist_stop_waits_for_inflight_thread_write(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    inserted: list[list[dict]] = []

    def slow_insert(batch: list[dict]) -> None:
        started.set()
        assert release.wait(timeout=2)
        inserted.append(batch)

    async def scenario() -> None:
        service = PersistService()
        monkeypatch.setattr(storage, "init_db", lambda: None)
        monkeypatch.setattr(storage, "abort_stale_running_experiments", lambda: 0)
        monkeypatch.setattr(storage, "insert_frames", slow_insert)
        await service.start()
        service.enqueue_frame({"seq": 1})

        stopping = asyncio.create_task(service.stop())
        assert await asyncio.to_thread(started.wait, 1)
        assert not stopping.done()
        release.set()
        await asyncio.wait_for(stopping, timeout=1)
        assert inserted == [[{"seq": 1}]]

    asyncio.run(scenario())


def test_persist_insert_failure_stops_accepting(monkeypatch):
    async def scenario() -> None:
        service = PersistService()
        monkeypatch.setattr(storage, "init_db", lambda: None)
        monkeypatch.setattr(storage, "abort_stale_running_experiments", lambda: 0)

        def boom(_batch: list[dict]) -> None:
            raise sqlite3.OperationalError("injected persist failure")

        monkeypatch.setattr(storage, "insert_frames", boom)
        await service.start()
        service.enqueue_frame({"seq": 1})
        with pytest.raises(sqlite3.OperationalError, match="injected persist failure"):
            await service.flush()
        assert service._accepting is False
        assert service.degraded is True
        assert service.snapshot()["persistence"] == "degraded"
        assert service.enqueue_frame({"seq": 2}) is False
        await service.stop()

    asyncio.run(scenario())


def test_persist_failure_is_visible_and_stop_still_finishes(tmp_path, monkeypatch):
    """P0-1/P1-2：insert 失败后 health 降级，stop 仍能 finish，不把实验留在 running。"""
    import time

    from app.acquisition import acquisition

    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "persist-degraded.db"))
    monkeypatch.setenv("EC_ENABLE_DEBUG_ENDPOINTS", "1")
    with TestClient(app) as client:
        start = client.post("/api/experiment/start")
        assert start.json()["ok"] is True
        exp_id = start.json()["experiment_id"]
        time.sleep(0.25)

        def boom(_batch: list[dict]) -> None:
            raise sqlite3.OperationalError("injected persist failure")

        monkeypatch.setattr(storage, "insert_frames", boom)
        time.sleep(0.8)

        health = client.get("/health").json()
        assert health["status"] == "ok"
        assert health["persistence"] == "degraded"
        assert "injected persist failure" in (health.get("persistence_error") or "")

        stop = client.post("/api/experiment/stop")
        assert stop.status_code == 200
        body = stop.json()
        assert body["ok"] is True
        assert body["status"] == "stopped"
        assert "落库失败" in (body.get("message") or "")
        assert "重启后端" in (body.get("message") or "")
        assert body.get("persistence") == "degraded"
        assert storage.get_experiment(exp_id)["status"] == "stopped"
        assert persist.degraded is True

    # lifespan 关停时 acquisition.stop() 复位会话状态，不把本轮的告警闩锁带进下一个 lifespan
    assert acquisition._persist_notice_sent is False


def test_persist_degraded_visible_to_late_ws_client(tmp_path, monkeypatch):
    """P1-A：一次性告警发出时无订阅者，晚连 WS 与 /current 仍能感知降级。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "late-ws.db"))
    with TestClient(app) as client:
        persist._error = RuntimeError("injected persist failure")
        persist._accepting = False
        acquisition._persist_notice_sent = True
        try:
            current = client.get("/api/experiment/current").json()
            assert current["persistence"] == "degraded"
            assert "重启后端" in (current.get("message") or "")
            with client.websocket_connect("/ws/stream") as ws:
                msg = ws.receive_json()
                assert "ec" not in msg
                assert msg["persistence"] == "degraded"
                assert "重启后端" in (msg.get("message") or "")
                assert "落库失败" in (msg.get("message") or "")
        finally:
            persist._error = None
            persist._accepting = True
            acquisition.reset_notices()


def test_resume_resets_persist_notice(tmp_path, monkeypatch):
    """P2-B：resume 清除一次性告警闩锁，续跑期间可再发降级提示。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "resume-notice.db"))
    with TestClient(app) as client:
        client.post("/api/experiment/start")
        client.post("/api/experiment/stop")
        acquisition._persist_notice_sent = True
        again = client.post("/api/experiment/start")
        assert again.json()["resumed"] is True
        assert acquisition._persist_notice_sent is False
        client.post("/api/experiment/reset")
