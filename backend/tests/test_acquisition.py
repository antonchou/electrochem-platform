"""采集循环与组帧：单一采集任务、组帧元数据、续跑边界去重、节拍与读数异常。"""

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import routes, storage
from app.drivers import DriverReading
from app.drivers.base import DriverConfig
from app.main import app
from app.persistence import persist
from app.state import state


def _start(client, sample_id: str) -> int:
    r = client.post("/api/experiment/start", json={"sample_id": sample_id})
    assert r.json()["ok"], r.text
    return r.json()["experiment_id"]


def test_no_websocket_client_still_persists(client):
    """P1-1：无任何 WS 连接时，运行中的实验同样生成并落库。"""
    exp_id = _start(client, "NACL_001")
    time.sleep(0.4)  # 10Hz 采集约 4 帧
    client.post("/api/experiment/stop")
    assert storage.count_frames(exp_id) > 0


def test_restart_writes_to_new_experiment(client):
    """P1-3：停止后再次开始，新帧写入新实验记录，旧实验帧数不变。"""
    e1 = _start(client, "A")
    time.sleep(0.3)
    client.post("/api/experiment/stop")
    n1 = storage.count_frames(e1)
    assert n1 > 0

    e2 = _start(client, "B")
    assert e2 != e1
    time.sleep(0.3)
    client.post("/api/experiment/stop")
    n2 = storage.count_frames(e2)
    assert n2 > 0
    # 旧实验没有被新帧污染
    assert storage.count_frames(e1) == n1


def test_sample_frame_count_updated(client):
    """P2-4：samples.frame_count 随帧写入累加，与 raw_frames 一致。"""
    exp_id = _start(client, "NACL_002")
    time.sleep(0.4)
    client.post("/api/experiment/stop")
    samples = storage.get_samples(exp_id)
    assert len(samples) == 1
    assert samples[0]["frame_count"] == storage.count_frames(exp_id)
    assert samples[0]["frame_count"] > 0


def test_mock_quality_flag_is_persisted(client):
    """集成后的 MockDriver 标记必须进入 raw_frames，实时协议保持不变。"""
    exp_id = _start(client, "MOCK_FLAGS")
    time.sleep(0.3)
    client.post("/api/experiment/stop")

    frames = storage.get_frames(exp_id, limit=10)
    assert frames
    assert all("SIMULATED" in (frame["quality_flags"] or "") for frame in frames)


def test_resume_boundary_dedup_helpers():
    """续跑边界去重：一次性窗口的装载/命中/消费语义。"""
    from app import routes

    # 无窗口时任何帧都不丢弃
    routes._clear_resume_boundary()
    frame = {"voltage_raw_v": 1.0, "current_raw_a": 1e-3, "temperature_raw_c": 27.0}
    assert routes._consume_resume_duplicate(frame) is False

    # 装载窗口：完全一致的原始三元组 → 命中丢弃
    routes._resume_boundary_raws = (1.0, 1e-3, 27.0)
    assert routes._consume_resume_duplicate(dict(frame)) is True

    # 窗口一次性：命中后关闭，同值帧不再丢弃（慢速源保持帧语义）
    assert routes._consume_resume_duplicate(dict(frame)) is False

    # 未命中（读数已前进）同样关闭窗口
    routes._resume_boundary_raws = (1.0, 1e-3, 27.0)
    moved = {"voltage_raw_v": 1.1, "current_raw_a": 1.1e-3, "temperature_raw_c": 27.0}
    assert routes._consume_resume_duplicate(moved) is False
    assert routes._consume_resume_duplicate(dict(frame)) is False

    routes._clear_resume_boundary()


def test_resume_boundary_window_loaded_then_consumed(client, monkeypatch):
    """续跑去重窗口接线：resume 从库装载最后一条帧的原始三元组，采集循环首帧消费。"""
    from app import routes

    exp_id = _start(client, "RESUME_WIN")
    time.sleep(0.3)
    client.post("/api/experiment/stop")
    last = storage.get_recent_frames(exp_id, limit=1)[0]
    expected = (last["voltage_raw_v"], last["current_raw_a"], last["temperature_raw"])

    # 拉长采样周期冻结采集 tick，消除「装载后被立即消费」的读数竞态
    monkeypatch.setattr(routes, "_sample_period_seconds", 1.0)
    time.sleep(0.15)  # 等在飞的 0.1s sleep 结束，循环进入 1s 长睡眠
    r = client.post("/api/experiment/start").json()
    assert r["resumed"] is True
    assert routes._resume_boundary_raws == expected, "resume 应从库装载边界基准"

    monkeypatch.setattr(routes, "_sample_period_seconds", 0.1)
    time.sleep(1.4)  # 循环醒来后首个 tick 应消费一次性窗口
    assert routes._resume_boundary_raws is None, "采集循环应消费窗口"

    client.post("/api/experiment/stop")
    client.post("/api/experiment/reset")


def test_calibration_claimed_follows_calibration_id_when_driver_silent(monkeypatch):
    """T-06：驱动未显式声明 claimed 时，有效校准 id 推导 claimed=True。"""
    cfg = DriverConfig(calibration_id=None)
    fake_driver = SimpleNamespace(config=cfg)
    monkeypatch.setattr(routes, "_driver", fake_driver)
    monkeypatch.setattr(state, "calibration_id", "ENV-CAL-01")
    try:
        params = routes._measurement_params()
    finally:
        monkeypatch.setattr(state, "calibration_id", None)
    assert params["calibration_id"] == "ENV-CAL-01"
    assert params["calibration_claimed"] is True


def test_calibration_claimed_explicit_false_wins(monkeypatch):
    """P2-5：驱动显式 calibration_claimed=False（有编号但未校准）不得被覆盖。"""
    cfg = DriverConfig(calibration_id="SIM-KCELL-1.0", calibration_claimed=False)
    fake_driver = SimpleNamespace(config=cfg)
    monkeypatch.setattr(routes, "_driver", fake_driver)
    monkeypatch.setattr(state, "calibration_id", "SIM-KCELL-1.0")
    try:
        params = routes._measurement_params()
    finally:
        monkeypatch.setattr(state, "calibration_id", None)
    assert params["calibration_claimed"] is False


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


@pytest.mark.parametrize("rate_hz", [20, 100])
def test_acquisition_rate_follows_configured_period(tmp_path, monkeypatch, rate_hz):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / f"rate{rate_hz}.db"))
    monkeypatch.setenv("EC_SAMPLE_RATE_HZ", str(rate_hz))
    with TestClient(app) as c:
        exp = c.post("/api/experiment/start").json()["experiment_id"]
        time.sleep(1.5)
        c.post("/api/experiment/stop")
        ts = [f["t_seconds"] for f in storage.get_frames(exp, limit=100_000)]
    assert len(ts) > 10
    rate = (len(ts) - 1) / (ts[-1] - ts[0])
    # 旧实现：Windows 下 20Hz 实为 ~16Hz（每次 sleep 向上取整到 15.6ms 倍数），100Hz 空转上千
    assert 0.85 * rate_hz <= rate <= 1.15 * rate_hz, f"配置 {rate_hz}Hz，实测 {rate:.1f}Hz"


def test_sleep_until_resyncs_instead_of_bursting(monkeypatch):
    """落后超过一个周期（读数阻塞）时从当前时刻重新对齐，不连发积压的周期。"""

    async def scenario():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(routes, "_sample_period_seconds", 0.05)
        stale = loop.time() - 1.0
        t0 = loop.time()
        adopted = await routes._sleep_until(loop, stale)
        assert adopted >= t0, "落后的截止时刻应被重置为当前时刻"
        assert loop.time() - t0 < 0.05

    asyncio.run(scenario())


def test_acquisition_errors_back_off_log_once_notify_and_recover(monkeypatch, caplog):
    class FlakyDriver:
        def __init__(self) -> None:
            self.fail = True
            self.fail_times: list[float] = []

        async def read(self, _elapsed: float) -> DriverReading:
            if self.fail:
                self.fail_times.append(time.perf_counter())
                raise OSError("ADC 掉线（测试注入）")
            return DriverReading(ec=1413.0, temperature=25.0)

    async def scenario():
        driver = FlakyDriver()
        published: list[dict] = []

        async def capture(payload: dict) -> int:
            published.append(payload)
            return 0

        await state.reset()
        await state.start(experiment_db_id=None)
        monkeypatch.setattr(routes, "_driver", driver)
        monkeypatch.setattr(routes, "_sample_period_seconds", 0.01)
        monkeypatch.setattr(routes, "broadcast", capture)
        task = asyncio.create_task(routes._acquisition_loop())
        await asyncio.sleep(0.6)
        driver.fail = False
        await asyncio.sleep(0.6)
        task.cancel()
        await task
        await state.reset()
        return driver, published

    with caplog.at_level(logging.INFO, logger="app.routes"):
        driver, published = asyncio.run(scenario())

    # 指数退避：重试间隔逐次拉长（旧实现固定 0.1s，一直按 10 次/秒重试）
    gaps = [b - a for a, b in zip(driver.fail_times, driver.fail_times[1:])]
    assert len(gaps) >= 4, driver.fail_times
    assert gaps[-1] >= 4 * gaps[0], gaps
    # 完整堆栈只记首条（旧实现每次失败一条，10 条/秒）
    tracebacks = [r for r in caplog.records if r.exc_info and "采集循环异常" in r.getMessage()]
    assert len(tracebacks) == 1, [r.getMessage() for r in caplog.records]
    # 前端只收到一次告警（旧实现只能等 3s 看门狗报“数据流超时”）
    alerts = [p for p in published if p.get("message") == routes.ACQUISITION_ERROR_MESSAGE]
    assert len(alerts) == 1 and alerts[0]["status"] == "running"
    # 恢复：记一条恢复日志并继续出帧
    assert any("采集恢复" in r.getMessage() for r in caplog.records)
    assert any("ec" in p for p in published)
