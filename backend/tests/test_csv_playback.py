"""CSV 回放驱动测试 + 数据接入集成测试。

覆盖：
- 驱动从 4 列 CSV 读取并回放（time_s/voltage_v/current/temperature_c）
- 电压 <= 0 时计算链抛错 -> 帧标记 COMPUTE_INVALID、原始数据仍落库、不崩溃
- 集成：EC_DRIVER=csv 起后端，跑实验，帧落库/广播正常
"""

import asyncio
import csv
import time

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.acquisition import acquisition, build_driver
from app.drivers import CsvPlaybackConfig, CsvPlaybackDriver
from app.main import app


def _write_csv(tmp_path, rows, name="data.csv"):
    """写一个 4 列 CSV：time_s,voltage_v,current,temperature_c。"""
    path = tmp_path / name
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "voltage_v", "current", "temperature_c"])
        for row in rows:
            w.writerow(row)
    return str(path)


def test_playback_reads_csv_and_replays(tmp_path):
    path = _write_csv(
        tmp_path,
        [
            (0.0, 1.0, 1.0e-3, 27.0),
            (0.1, 1.1, 1.1e-3, 27.0),
            (0.2, 1.2, 1.2e-3, 27.0),
        ],
    )
    cfg = CsvPlaybackConfig(path=path)

    async def scenario():
        d = CsvPlaybackDriver(cfg)
        await d.connect()
        assert d.connected
        r0 = await d.read(0.0)
        assert r0.voltage_v == 1.0
        assert r0.current_a == 1.0e-3
        assert r0.temperature == 27.0
        assert r0.quality_flags == ("CSV", "PLAYBACK")
        r1 = await d.read(0.15)
        assert r1.voltage_v == 1.1
        await d.close()
        assert not d.connected

    asyncio.run(scenario())


def test_playback_speed_scales_elapsed(tmp_path):
    path = _write_csv(
        tmp_path,
        [
            (0.0, 1.0, 1.0e-3, 27.0),
            (0.5, 1.5, 1.5e-3, 27.0),
            (1.0, 2.0, 2.0e-3, 27.0),
        ],
    )
    cfg = CsvPlaybackConfig(path=path, speed=10.0)

    async def scenario():
        d = CsvPlaybackDriver(cfg)
        await d.connect()
        r = await d.read(0.05)
        assert r.voltage_v == 1.5
        eof = await d.read(0.2)
        assert eof.quality_flags == ("CSV", "EOF")
        await d.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("speed", [0.0, float("nan"), float("inf")])
def test_playback_rejects_nonpositive_or_nonfinite_speed(speed):
    with pytest.raises(ValueError, match="speed"):
        CsvPlaybackConfig(path="x.csv", speed=speed)


def test_incomplete_eof_logs_once(caplog):
    acquisition._quiet_incomplete_flags.clear()
    with caplog.at_level("INFO"):
        acquisition._log_incomplete_reading(("CSV", "EOF"))
        acquisition._log_incomplete_reading(("CSV", "EOF"))
    messages = [r.message for r in caplog.records if "回放结束" in r.message]
    assert len(messages) == 1
    acquisition._quiet_incomplete_flags.clear()


def test_playback_eof_marks_quality(tmp_path):
    path = _write_csv(tmp_path, [(0.0, 1.0, 1e-3, 27.0), (0.1, 1.1, 1.1e-3, 27.0)])
    cfg = CsvPlaybackConfig(path=path)

    async def scenario():
        d = CsvPlaybackDriver(cfg)
        await d.connect()
        r = await d.read(100.0)
        assert r.quality_flags == ("CSV", "EOF")
        await d.close()

    asyncio.run(scenario())


def test_playback_missing_file_raises(tmp_path):
    cfg = CsvPlaybackConfig(path=str(tmp_path / "nope.csv"))

    async def scenario():
        d = CsvPlaybackDriver(cfg)
        with pytest.raises(FileNotFoundError):
            await d.connect()

    asyncio.run(scenario())


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """用 CSV 回放驱动起后端：EC_DRIVER=csv + EC_CSV_PATH。"""
    # 模拟真实 CV：0~1s 密集时间戳（0.01s 步进），电位先升后降、含正负
    rows = []
    t = 0.0
    while t <= 1.0:
        v = 1.0 - abs(t - 0.5) * 4.0  # 0.5s 峰 1.0V，两端 -1.0V
        i = 1.0e-3 * (1.0 + 0.2 * abs(v))
        rows.append((round(t, 2), round(v, 3), i, 27.0))
        t += 0.01
    path = _write_csv(tmp_path, rows, name="ingest.csv")
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "ingest.db"))
    monkeypatch.setenv("EC_ENABLE_DEBUG_ENDPOINTS", "1")
    monkeypatch.setenv("EC_DRIVER", "csv")
    monkeypatch.setenv("EC_CSV_PATH", path)
    with TestClient(app) as c:
        yield c


def test_csv_resume_continues_timeline(client):
    """暂停墙钟不得让 CSV 跳到后段。"""
    from app import storage

    exp_id = client.post("/api/experiment/start").json()["experiment_id"]
    time.sleep(0.25)
    client.post("/api/experiment/stop")
    first = storage.get_frames(exp_id, limit=500)
    assert first
    last_t = first[-1]["t_seconds"] or 0.0
    time.sleep(0.35)
    resumed = client.post("/api/experiment/start").json()
    assert resumed["resumed"] is True
    time.sleep(0.25)
    client.post("/api/experiment/stop")
    all_frames = storage.get_frames(exp_id, limit=500)
    newer = [f for f in all_frames if (f["t_seconds"] or 0) > last_t + 1e-6]
    assert newer, "续跑应追加帧"
    assert newer[0]["t_seconds"] < last_t + 0.25
    client.post("/api/experiment/reset")


def test_csv_driver_start_and_persist(client):
    """CSV 驱动跑实验：帧落库，正电压帧算出 k25，负电压帧标记 COMPUTE_INVALID。"""
    from app import storage

    exp_id = client.post("/api/experiment/start").json()["experiment_id"]
    time.sleep(1.0)
    client.post("/api/experiment/stop")
    client.post("/api/experiment/reset")

    frames = storage.get_frames(exp_id, limit=100)
    assert len(frames) >= 3
    computed = [f for f in frames if f["voltage_raw_v"] and f["voltage_raw_v"] > 0]
    assert computed, "应有正电压帧"
    valid = [f for f in computed if f.get("kappa_25_us_cm") is not None]
    assert valid, "正电压帧应算出 k25"
    invalid = [f for f in frames if f["voltage_raw_v"] and f["voltage_raw_v"] < 0]
    for f in invalid:
        assert "COMPUTE_INVALID" in (f.get("quality_flags") or "")
        assert "UNCALIBRATED" in (f.get("quality_flags") or "")
    for f in frames:
        assert f.get("calibration_id") == "UNCALIBRATED"
        assert "UNCALIBRATED" in (f.get("quality_flags") or "")

    detail = client.get(f"/api/experiments/{exp_id}").json()
    assert detail["calibrations"]
    cal = detail["calibrations"][0]
    assert cal["calibration_id"] == "UNCALIBRATED"
    assert "KCl" not in (cal.get("standard") or "")
    assert "playback" in (cal.get("standard") or "").lower()
    assert cal.get("coeff_value") is None
    assert cal.get("lot") in (None, "")


@pytest.fixture()
def constant_client(tmp_path, monkeypatch):
    """恒定值 CSV 回放后端：所有行 U/I/T 相同，续跑首帧必命中去重窗口。"""
    rows = [(round(t, 2), 1.0, 1.0e-3, 27.0) for t in [i * 0.01 for i in range(1000)]]
    path = _write_csv(tmp_path, rows, name="constant.csv")
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "constant.db"))
    monkeypatch.setenv("EC_ENABLE_DEBUG_ENDPOINTS", "1")
    monkeypatch.setenv("EC_DRIVER", "csv")
    monkeypatch.setenv("EC_CSV_PATH", path)
    with TestClient(app) as c:
        yield c


def test_csv_resume_dedups_boundary_sample(constant_client):
    """续跑首帧重采样停止前边界读数时必须丢弃，不重复落库（review M1）。

    恒定值 CSV 下续跑首个读数与停止前最后一条落库帧的 U/I/T 必然一致；
    去重后，续跑段第一帧的 t_seconds 应比停止点晚至少一个采样周期。
    """
    from app import storage

    exp_id = constant_client.post("/api/experiment/start").json()["experiment_id"]
    time.sleep(0.25)
    constant_client.post("/api/experiment/stop")
    first = storage.get_frames(exp_id, limit=500)
    assert first
    last_t = first[-1]["t_seconds"] or 0.0

    time.sleep(0.15)
    resumed = constant_client.post("/api/experiment/start").json()
    assert resumed["resumed"] is True
    time.sleep(0.35)
    constant_client.post("/api/experiment/stop")
    constant_client.post("/api/experiment/reset")

    all_frames = storage.get_frames(exp_id, limit=500)
    newer = [f for f in all_frames if (f["t_seconds"] or 0.0) > last_t + 1e-6]
    assert newer, "续跑应追加帧"
    # 去重窗口生效：续跑段没有帧落在停止点后不足一个采样周期的区间内
    boundary = [f for f in newer if (f["t_seconds"] or 0.0) < last_t + 0.09]
    assert not boundary, f"续跑首帧未去重: {[(f['t_seconds'], f['seq_no']) for f in boundary]}"


def test_csv_playback_loop_degenerate_timestamps(tmp_path, monkeypatch):
    csv_path = tmp_path / "zero_t.csv"
    csv_path.write_text(
        "time_s,voltage_v,current,temperature_c\n"
        "0.0,0.5,0.001,25.0\n"
        "0.0,0.6,0.002,25.1\n",
        encoding="utf-8",
    )
    driver = CsvPlaybackDriver(CsvPlaybackConfig(path=str(csv_path), loop=True))

    async def scenario():
        await driver.connect()
        first = await driver.read(0.0)
        later = await driver.read(3600.0)  # 远超末尾时间戳：必须回绕到首行而非病态取模
        return first, later

    first, later = asyncio.run(scenario())
    assert "EOF" not in first.quality_flags
    assert "EOF" not in later.quality_flags
    assert later.voltage_v == first.voltage_v  # 固定回放首行
    assert later.temperature == first.temperature


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
    real_consume = acquisition._consume_resume_duplicate

    def spy(frame):
        boundary_ready.append(acquisition._resume_boundary_raws is not None)
        return real_consume(frame)

    monkeypatch.setattr(storage, "get_recent_frames", slow_recent)
    monkeypatch.setattr(acquisition, "_consume_resume_duplicate", spy)
    assert constant_client.post("/api/experiment/start").json()["resumed"] is True
    time.sleep(0.45)
    constant_client.post("/api/experiment/stop")
    constant_client.post("/api/experiment/reset")

    assert boundary_ready, "续跑后应至少产出一帧"
    assert boundary_ready[0] is True, f"续跑首帧比对时基准尚未装载: {boundary_ready}"
    assert not any(boundary_ready[1:]), "一次性窗口应由首帧消费"


def test_nonfinite_rows_are_skipped(tmp_path):
    """nan/inf 单元格与缺列同样按坏行跳过（nan 时间戳还会打乱排序）。"""
    path = _write_csv(
        tmp_path,
        [(0.0, 1.0, 1e-3, 25.0), ("nan", 1.0, 1e-3, 25.0), (0.1, 1.0, 1e-3, "nan"), (0.2, "inf", 1e-3, 25.0), (0.3, 1.1, 1.1e-3, 25.0)],
    )

    async def scenario():
        d = CsvPlaybackDriver(CsvPlaybackConfig(path=path))
        await d.connect()
        return d._times

    assert asyncio.run(scenario()) == [0.0, 0.3]


def test_csv_env_parse_error_names_the_variable(tmp_path, monkeypatch):
    path = _write_csv(tmp_path, [(0.0, 1.0, 1e-3, 25.0)])
    monkeypatch.setenv("EC_DRIVER", "csv")
    monkeypatch.setenv("EC_CSV_PATH", path)
    monkeypatch.setenv("EC_CSV_SPEED", "2x")
    with pytest.raises(ValueError, match="EC_CSV_SPEED"):
        build_driver()
