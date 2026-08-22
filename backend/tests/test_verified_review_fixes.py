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
from app.persistence import PersistenceUnavailableError, PersistService, persist
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
            return DriverReading(voltage_raw_v=1.0, current_raw_a=0.001, temperature_raw_c=25.0)

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

        assert published == []
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


def test_dropout_is_persisted_and_broadcast_as_quality_frame(monkeypatch):
    class DropoutThenBlockDriver:
        def __init__(self) -> None:
            self.read_count = 0
            self.block = asyncio.Event()

        async def read(self, _elapsed: float) -> DriverReading:
            self.read_count += 1
            if self.read_count == 1:
                return DriverReading(quality_flags=("SIMULATED", "DROPOUT"))
            await self.block.wait()
            raise AssertionError("unreachable")

    async def scenario() -> None:
        driver = DropoutThenBlockDriver()
        published: list[dict] = []
        persisted: list[dict] = []
        got_frame = asyncio.Event()

        async def capture(payload: dict) -> int:
            published.append(payload)
            got_frame.set()
            return 0

        await state.reset()
        await state.start(experiment_db_id=303, experiment_uid="EXP-DROPOUT")
        monkeypatch.setattr(routes, "_driver", driver)
        monkeypatch.setattr(routes, "_sample_period_seconds", 0.001)
        monkeypatch.setattr(routes, "broadcast", capture)
        monkeypatch.setattr(persist, "enqueue_frame", persisted.append)

        task = asyncio.create_task(routes._acquisition_loop())
        await asyncio.wait_for(got_frame.wait(), timeout=1)
        task.cancel()
        await task
        await state.reset()

        assert published[0]["message_type"] == "measurement"
        assert published[0]["quality_flags"] == ["SIMULATED", "DROPOUT"]
        assert persisted[0]["quality_flags"] == "SIMULATED|DROPOUT"

    asyncio.run(scenario())


def test_persist_failure_stops_accepting_and_flush_fails(monkeypatch):
    def fail_insert(_batch: list[dict]) -> None:
        raise RuntimeError("disk full")

    async def scenario() -> None:
        service = PersistService(queue_size=2)
        monkeypatch.setattr(storage, "init_db", lambda: None)
        monkeypatch.setattr(storage, "insert_frames", fail_insert)
        await service.start()
        service.enqueue_frame({"seq": 1})
        await asyncio.sleep(0.6)

        assert service._task is not None and service._task.done()
        assert service._accepting is False
        with pytest.raises(PersistenceUnavailableError, match="has failed"):
            service.enqueue_frame({"seq": 2})
        with pytest.raises(PersistenceUnavailableError, match="has failed"):
            await service.flush()
        await service.stop()

    asyncio.run(scenario())


def test_reset_and_start_are_serialized(monkeypatch):
    async def scenario() -> None:
        flush_started = asyncio.Event()
        release_flush = asyncio.Event()

        async def slow_flush() -> None:
            flush_started.set()
            await release_flush.wait()

        async def noop_finish(_exp_id: int, _status: str) -> None:
            return None

        async def create_new(**_kwargs) -> int:
            return 202

        async def no_subscribers(_payload: dict) -> int:
            return 0

        monkeypatch.setattr(persist, "flush", slow_flush)
        monkeypatch.setattr(persist, "finish_experiment", noop_finish)
        monkeypatch.setattr(persist, "create_experiment_with_sample", create_new)
        monkeypatch.setattr(routes, "broadcast", no_subscribers)

        await state.reset()
        await state.start(experiment_db_id=101, experiment_uid="EXP-OLD")
        resetting = asyncio.create_task(routes.reset())
        await flush_started.wait()
        starting = asyncio.create_task(routes.start())
        await asyncio.sleep(0)
        assert not starting.done()

        release_flush.set()
        assert (await resetting).status == "idle"
        started = await starting
        assert started.status == "running"
        assert state.experiment_db_id == 202
        await state.reset()

    asyncio.run(scenario())


def test_lifespan_aborts_leaked_run_and_resets_state(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "lifespan.db"))
    with TestClient(app) as client:
        exp_id = client.post("/api/experiment/start").json()["experiment_id"]
        assert state.status == "running"

    assert state.status == "idle"
    assert storage.get_experiment(exp_id)["status"] == "aborted"
