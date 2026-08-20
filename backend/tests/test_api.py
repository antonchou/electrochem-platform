"""pytest + FastAPI TestClient 覆盖：协议格式、控制状态机、持久化与导出 API。"""

import os
import queue
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path):
    """带 lifespan 的 TestClient：启动/关闭持久化服务；DB 指向临时文件。"""
    os.environ["EC_DB_PATH"] = str(tmp_path / "api_test.db")
    with TestClient(app) as c:
        yield c
    os.environ.pop("EC_DB_PATH", None)


def _receive_json_with_timeout(ws, timeout: float):
    """带超时的 receive_json（TestClient 的 receive 不支持 timeout 参数）。"""
    result: queue.Queue = queue.Queue()

    def worker():
        try:
            result.put(ws.receive_json())
        except Exception as exc:  # noqa: BLE001
            result.put(exc)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None
    res = result.get()
    if isinstance(res, Exception):
        raise res
    return res


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_control_state_machine(client):
    # idle -> running
    r = client.post("/api/experiment/start")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "running"
    assert body["experiment_id"] is not None

    # 重复 start 应拒绝
    r = client.post("/api/experiment/start")
    assert r.json()["ok"] is False

    # running -> stopped
    r = client.post("/api/experiment/stop")
    assert r.json()["status"] == "stopped"

    # stopped -> idle
    r = client.post("/api/experiment/reset")
    assert r.json()["status"] == "idle"


def test_ws_stream_protocol(client):
    """WS 连接后，start 会推送符合协议的实时帧（可能先收到状态广播，需跳过）。"""
    with client.websocket_connect("/ws/stream") as ws:
        assert client.post("/api/experiment/start").json()["status"] == "running"
        data = None
        for _ in range(10):
            msg = ws.receive_json()
            if "ec" in msg:
                data = msg
                break
        assert data is not None, "未收到数据帧"
        assert set(data) == {"timestamp", "ec", "temperature", "status"}
        assert data["status"] == "running"
        client.post("/api/experiment/stop")
    client.post("/api/experiment/reset")


def test_ws_stop_stops_stream(client):
    """停止后：收到 stopped 状态广播，且 300ms 内不再有数据帧。"""
    with client.websocket_connect("/ws/stream") as ws:
        client.post("/api/experiment/start")
        for _ in range(10):
            if "ec" in ws.receive_json():
                break
        client.post("/api/experiment/stop")
        status_frame = _receive_json_with_timeout(ws, 0.5)
        assert status_frame is not None
        assert "ec" not in status_frame
        assert status_frame["status"] == "stopped"
        assert _receive_json_with_timeout(ws, 0.3) is None
    client.post("/api/experiment/reset")


def test_debug_bad_frame(client):
    """bad-frame 调试接口可被调用；前端容错由 E2E 验证。"""
    with client.websocket_connect("/ws/stream") as ws:
        client.post("/api/experiment/start")
        for _ in range(10):
            if "ec" in ws.receive_json():
                break
        r = client.post("/api/debug/bad-frame")
        assert r.json()["ok"] is True
        bad = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("ec") == "abc":
                bad = msg
                break
        assert bad is not None, "未收到坏帧"
        client.post("/api/experiment/stop")
    client.post("/api/experiment/reset")


# ---------------- Phase 7：持久化与历史 API ----------------


def test_start_creates_experiment_record(client):
    """start 带样品参数 → 历史列表与详情可见溯源字段。"""
    r = client.post(
        "/api/experiment/start",
        json={"sample_id": "NACL_004", "sensor_path_id": "CM2_WIDE", "title": "NaCl 梯度"},
    )
    body = r.json()
    assert body["ok"] is True
    exp_id = body["experiment_id"]

    client.post("/api/experiment/stop")

    items = client.get("/api/experiments").json()
    assert any(x["id"] == exp_id for x in items)

    detail = client.get(f"/api/experiments/{exp_id}").json()
    assert detail["sample_id"] == "NACL_004"
    assert detail["sensor_path_id"] == "CM2_WIDE"
    assert detail["samples"][0]["sample_id"] == "NACL_004"
    client.post("/api/experiment/reset")


def test_frames_persist_and_export(client):
    """运行期间帧落库；export.csv 与 export.json 可取回。"""
    with client.websocket_connect("/ws/stream") as ws:
        exp_id = client.post(
            "/api/experiment/start", json={"sample_id": "BLANK", "sensor_path_id": "BA121S_LOW"}
        ).json()["experiment_id"]
        got = 0
        for _ in range(15):
            msg = ws.receive_json()
            if "ec" in msg:
                got += 1
            if got >= 3:
                break
        client.post("/api/experiment/stop")
    client.post("/api/experiment/reset")

    detail = client.get(f"/api/experiments/{exp_id}").json()
    assert detail["frame_count"] >= 3

    csv_resp = client.get(f"/api/experiments/{exp_id}/export.csv")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    csv_text = csv_resp.text
    assert "ec_raw_us_cm" in csv_text
    assert "BA121S_LOW" in csv_text

    json_resp = client.get(f"/api/experiments/{exp_id}/export.json").json()
    assert len(json_resp["frames"]) >= 3
    assert json_resp["frames"][0]["sensor_path_id"] == "BA121S_LOW"


def test_experiment_404(client):
    assert client.get("/api/experiments/999999").status_code == 404
    assert client.get("/api/experiments/999999/export.csv").status_code == 404
