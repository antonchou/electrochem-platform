"""Code Review 修复回归测试（P1-1/P1-3/P2-4）。

覆盖：
- P1-1 无浏览器连接时实验仍落库（采集由单一后台任务负责）
- P1-3 再次开始时帧写入新实验记录，不混入旧实验
- P2-4 samples.frame_count 随帧写入正确累加
"""

import os
import time

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.main import app


@pytest.fixture()
def client(tmp_path):
    os.environ["EC_DB_PATH"] = str(tmp_path / "review_test.db")
    with TestClient(app) as c:
        yield c
    os.environ.pop("EC_DB_PATH", None)


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
