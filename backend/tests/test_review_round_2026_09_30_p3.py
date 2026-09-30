"""2026-09-30 审查 P3 修复回归测试（R3-5 / R3-6 / R3-10 / R3-11；R3-8 见 conftest.py）。

- R3-5 续跑去重基准先装载再进入 running：读库再慢，被比对的也是续跑首帧
- R3-6 帧查询 mode=head/tail/even + total；等间隔抽样覆盖整条实验
- R3-10 采集按截止时刻调度：实际采样率贴合配置（旧实现周期叠加处理耗时；
  Windows 上周期短于 15.6ms 时循环空转）
- R3-11 读数持续抛错：指数退避、完整堆栈只记一条、前端告警一次、恢复后继续出帧
"""

import asyncio
import csv
import logging
import time

import pytest
from fastapi.testclient import TestClient

from app import routes, storage
from app.drivers import DriverReading
from app.main import app
from app.state import state

SENSOR = "MOCK_EC_IV"


def _insert_frames(exp_id: int, n: int) -> None:
    storage.insert_frames(
        [
            {
                "experiment_id": exp_id,
                "sample_id": "S",
                "sensor_path_id": SENSOR,
                "seq_no": i + 1,
                "t_seconds": round(i * 0.1, 1),
                "ec_raw": 1413.0,
                "temperature_raw": 25.0,
                "kappa_25_us_cm": 1413.0,
            }
            for i in range(n)
        ]
    )


# ---------------- R3-6 帧查询 ----------------


def test_frames_modes_head_tail_even(client):
    exp = storage.create_experiment_with_sample(
        experiment_id="EXP-FR", title="t", sample_id="S", sensor_path_id=SENSOR
    )
    _insert_frames(exp, 1000)
    url = f"/api/experiments/{exp}/frames"

    head = client.get(url, params={"limit": 50})
    assert head.headers["content-type"].startswith("application/json")
    body = head.json()
    assert (len(body["frames"]), body["total"], body["mode"]) == (50, 1000, "head")
    assert body["frames"][0]["t_seconds"] == 0.0

    # tail：最新 N 条，按时间正序（续跑水合只需要尾部）
    tail = client.get(url, params={"limit": 100, "mode": "tail"}).json()
    ts = [f["t_seconds"] for f in tail["frames"]]
    assert (len(ts), ts[0], ts[-1]) == (100, 90.0, 99.9)
    assert ts == sorted(ts)

    # even：全实验等间隔，保留首末帧（与前端 downsample 同口径 floor(i·(n−1)/(max−1))）
    even = client.get(url, params={"limit": 10, "mode": "even"}).json()
    seqs = [f["seq_no"] for f in even["frames"]]
    assert seqs == [1 + (i * 999) // 9 for i in range(10)]
    assert (seqs[0], seqs[-1], even["total"]) == (1, 1000, 1000)

    # 上限超过总数：全量返回
    assert len(client.get(url, params={"limit": 5000, "mode": "even"}).json()["frames"]) == 1000
    # offset 只属于 head 分页
    assert client.get(url, params={"limit": 10, "mode": "tail", "offset": 5}).status_code == 400
    assert client.get(url, params={"mode": "bogus"}).status_code == 422


def test_get_frames_even_edge_cases(client):
    exp = storage.create_experiment_with_sample(
        experiment_id="EXP-EV", title="t", sample_id="S", sensor_path_id=SENSOR
    )
    assert storage.get_frames_even(exp, max_points=10) == []
    _insert_frames(exp, 3)
    assert [f["seq_no"] for f in storage.get_frames_even(exp, max_points=1)] == [1]
    assert [f["seq_no"] for f in storage.get_frames_even(exp, max_points=2)] == [1, 3]
    with pytest.raises(ValueError):
        storage.get_frames_even(exp, max_points=0)


# ---------------- R3-5 续跑去重基准的装载顺序 ----------------


@pytest.fixture()
def constant_client(tmp_path, monkeypatch):
    path = tmp_path / "constant.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "voltage_v", "current", "temperature_c"])
        for i in range(1000):
            w.writerow((round(i * 0.01, 2), 1.0, 1.0e-3, 27.0))
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "constant.db"))
    monkeypatch.setenv("EC_DRIVER", "csv")
    monkeypatch.setenv("EC_CSV_PATH", str(path))
    with TestClient(app) as c:
        yield c


def test_resume_boundary_is_loaded_before_first_resumed_frame(constant_client, monkeypatch):
    exp_id = constant_client.post("/api/experiment/start").json()["experiment_id"]
    time.sleep(0.3)
    constant_client.post("/api/experiment/stop")

    # 读库比一个采样周期（0.1s）还慢：旧顺序（先 resume 再装载）下，装载期间采集循环
    # 已产出续跑首帧，比对时基准还是 None → 首帧漏检
    real_recent = storage.get_recent_frames

    def slow_recent(exp, *, limit=500):
        if limit == 1:
            time.sleep(0.25)
        return real_recent(exp, limit=limit)

    boundary_ready: list[bool] = []
    real_consume = routes._consume_resume_duplicate

    def spy(frame):
        boundary_ready.append(routes._resume_boundary_raws is not None)
        return real_consume(frame)

    monkeypatch.setattr(storage, "get_recent_frames", slow_recent)
    monkeypatch.setattr(routes, "_consume_resume_duplicate", spy)
    assert constant_client.post("/api/experiment/start").json()["resumed"] is True
    time.sleep(0.45)
    constant_client.post("/api/experiment/stop")
    constant_client.post("/api/experiment/reset")

    assert boundary_ready, "续跑后应至少产出一帧"
    assert boundary_ready[0] is True, f"续跑首帧比对时基准尚未装载: {boundary_ready}"
    assert not any(boundary_ready[1:]), "一次性窗口应由首帧消费"


# ---------------- R3-10 截止时刻节拍 ----------------


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


# ---------------- R3-11 读数持续抛错 ----------------


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
