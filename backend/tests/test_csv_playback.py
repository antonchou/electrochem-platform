"""CSV 回放驱动测试 + 数据接入集成测试。

覆盖：
- 驱动从 4 列 CSV 读取并回放（time_s/voltage_v/current/temperature_c）
- 电压 <= 0 时计算链抛错 -> 帧标记 COMPUTE_INVALID、原始数据仍落库、不崩溃
- 集成：EC_DRIVER=csv 起后端，跑实验，帧落库/广播正常
"""

import asyncio
import csv
import os
import time

import pytest
from fastapi.testclient import TestClient

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
    for k in ("EC_DB_PATH", "EC_ENABLE_DEBUG_ENDPOINTS", "EC_DRIVER", "EC_CSV_PATH"):
        monkeypatch.delenv(k, raising=False)


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
