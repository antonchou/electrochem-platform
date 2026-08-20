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
