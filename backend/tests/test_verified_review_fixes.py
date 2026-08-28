"""对 REVIEW.md 核实后确认的并发、事务与持久化问题做回归保护。"""

import asyncio
import os
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app import routes, storage
from app.drivers.base import DriverReading
from app.main import app
from app.persistence import PersistService, persist
from app.state import state


def _frame(experiment_id: int, sensor_path_id: str) -> dict:
    return {
        "experiment_id": experiment_id,
        "sample_id": "SAME",
        "sensor_path_id": sensor_path_id,
        "seq_no": 1,
        "timestamp_utc": "2026-08-21T00:00:00Z",
        "monotonic_ms": 1,
        "t_seconds": 0.1,
        "ec_raw": 100.0,
        "temperature_raw": 25.0,
        "k25": None,
        "quality_flags": None,
        "status": "running",
    }


def test_frame_count_isolated_by_sensor_path(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "paths.db"))
    storage.init_db()
    exp_id = storage.create_experiment("EXP-PATHS", "paths")
    storage.upsert_sample(exp_id, "SAME", "WIDE")
    storage.upsert_sample(exp_id, "SAME", "NARROW")

    storage.insert_frames([_frame(exp_id, "WIDE")])

    samples = {item["sensor_path_id"]: item["frame_count"] for item in storage.get_samples(exp_id)}
    assert samples == {"WIDE": 1, "NARROW": 0}


def test_insert_frames_upserts_missing_sample(tmp_path, monkeypatch):
    """P0-2：帧写入时样品行不存在则补建并累计 frame_count。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "upsert-count.db"))
    storage.init_db()
    exp_id = storage.create_experiment("EXP-UPSERT", "upsert")
    storage.insert_frames([_frame(exp_id, "WIDE")])
    storage.insert_frames([_frame(exp_id, "WIDE")])
    samples = {item["sensor_path_id"]: item["frame_count"] for item in storage.get_samples(exp_id)}
    assert samples == {"WIDE": 2}


def test_experiment_and_sample_creation_rolls_back_together(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "atomic.db"))
    storage.init_db()

    with pytest.raises(sqlite3.IntegrityError):
        storage.create_experiment_with_sample(
            "EXP-ATOMIC",
            "atomic",
            None,  # type: ignore[arg-type]  # 强制触发 samples.sample_id NOT NULL
            "WIDE",
        )

    assert storage.list_experiments() == []


def test_start_storage_failure_does_not_enter_running(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "start-failure.db"))

    async def fail_create(**_kwargs):
        raise RuntimeError("injected create failure")

    with TestClient(app) as client:
        monkeypatch.setattr(persist, "create_experiment_with_sample", fail_create)
        with pytest.raises(RuntimeError, match="injected create failure"):
            client.post("/api/experiment/start")
        assert client.get("/health").json()["experiment"] == "idle"
        assert storage.list_experiments() == []


def test_read_completed_after_stop_is_discarded(monkeypatch):
    class DelayedDriver:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def read(self, _elapsed: float) -> DriverReading:
            self.started.set()
            await self.release.wait()
            return DriverReading(ec=123.0, temperature=25.0)

    async def scenario() -> None:
        driver = DelayedDriver()
        published: list[dict] = []
        persisted: list[dict] = []

        async def capture(payload: dict) -> int:
            published.append(payload)
            return 0

        await state.reset()
        await state.start(experiment_db_id=101, experiment_uid="EXP-OLD")
        monkeypatch.setattr(routes, "_driver", driver)
        monkeypatch.setattr(routes, "_sample_period_seconds", 0.001)
        monkeypatch.setattr(routes, "broadcast", capture)
        monkeypatch.setattr(persist, "enqueue_frame", persisted.append)

        task = asyncio.create_task(routes._acquisition_loop())
        await asyncio.wait_for(driver.started.wait(), timeout=1)
        await state.stop()
        driver.release.set()
        await asyncio.sleep(0.02)
        task.cancel()
        await task
        await state.reset()

        assert not any("ec" in item for item in published)
        assert persisted == []

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


def test_startup_aborts_leftover_running_row(tmp_path, monkeypatch):
    """P1-B：进程启动时把历史遗留 running 行标 aborted，不影响已结束行与新实验。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "stale-running.db"))
    storage.init_db()
    leftover = storage.create_experiment("EXP-STALE", "crash leftover")
    stopped = storage.create_experiment("EXP-OK", "already stopped")
    storage.finish_experiment(stopped, "stopped")
    assert storage.get_experiment(leftover)["status"] == "running"
    assert storage.get_experiment(leftover)["ended_at_utc"] is None

    with TestClient(app) as client:
        stale = storage.get_experiment(leftover)
        assert stale["status"] == "aborted"
        assert stale["ended_at_utc"] is not None
        assert storage.get_experiment(stopped)["status"] == "stopped"
        assert client.get("/health").json()["experiment"] == "idle"
        started = client.post("/api/experiment/start").json()
        assert started["ok"] is True
        assert started["experiment_id"] != leftover
        client.post("/api/experiment/reset")


def test_lifespan_aborts_leaked_run_and_resets_state(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "lifespan.db"))
    with TestClient(app) as client:
        exp_id = client.post("/api/experiment/start").json()["experiment_id"]
        assert state.status == "running"

    assert state.status == "idle"
    assert storage.get_experiment(exp_id)["status"] == "aborted"


def test_persist_failure_is_visible_and_stop_still_finishes(tmp_path, monkeypatch):
    """P0-1/P1-2：insert 失败后 health 降级，stop 仍能 finish，不把实验留在 running。"""
    import time

    from app import routes

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

    routes._reset_persist_notice()


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


def test_persist_degraded_visible_to_late_ws_client(tmp_path, monkeypatch):
    """P1-A：一次性告警发出时无订阅者，晚连 WS 与 /current 仍能感知降级。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "late-ws.db"))
    with TestClient(app) as client:
        persist._error = RuntimeError("injected persist failure")
        persist._accepting = False
        routes._persist_notice_sent = True
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
            routes._reset_persist_notice()


def test_resume_resets_persist_notice(tmp_path, monkeypatch):
    """P2-B：resume 清除一次性告警闩锁，续跑期间可再发降级提示。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "resume-notice.db"))
    with TestClient(app) as client:
        client.post("/api/experiment/start")
        client.post("/api/experiment/stop")
        routes._persist_notice_sent = True
        again = client.post("/api/experiment/start")
        assert again.json()["resumed"] is True
        assert routes._persist_notice_sent is False
        client.post("/api/experiment/reset")
