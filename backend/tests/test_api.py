"""pytest + FastAPI TestClient 覆盖：协议格式、控制状态机、持久化与导出 API。"""

import os
import queue
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(tmp_path):
    """带 lifespan 的 TestClient：启动/关闭持久化服务；DB 指向临时文件。"""
    os.environ["EC_DB_PATH"] = str(tmp_path / "api_test.db")
    os.environ["EC_ENABLE_DEBUG_ENDPOINTS"] = "1"
    with TestClient(app) as c:
        yield c
    os.environ.pop("EC_DB_PATH", None)
    os.environ.pop("EC_ENABLE_DEBUG_ENDPOINTS", None)


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


# 带完整校准/激励上下文的 start 请求。
CALIBRATED_START = {
    "sample_id": "NACL_004",
    "sensor_path_id": "EC_IV_CELL_01",
    "calibration_id": "CAL_TEST_001",
    "cell_constant_cm_inv": 1.0,
    "alpha_per_c": 0.02,
    "compensation_model": "linear",
    "excitation_frequency_hz": 1000,
    "excitation_amplitude_v": 0.4,
    "range_id": "R_100R_10K",
}


def _has_measure_data(msg: dict) -> bool:
    """在线链路只承认显式 V2 measurement，禁止用 V1 字段猜测类型。"""
    return msg.get("message_type") == "measurement" and msg.get("schema_version") == "2.0"


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
    """WS 连接后，start 推送符合 V2 协议的实时帧（可能先收到状态广播，需跳过）。"""
    with client.websocket_connect("/ws/stream") as ws:
        assert client.post("/api/experiment/start", json=CALIBRATED_START).json()["status"] == "running"
        data = None
        for _ in range(10):
            msg = ws.receive_json()
            if _has_measure_data(msg):
                data = msg
                break
        assert data is not None, "未收到数据帧"
        # V2 溯源/状态字段
        assert data["schema_version"] == "2.0"
        assert data["message_type"] == "measurement"
        assert data["experiment_uid"].startswith("EXP-")
        assert data["seq_no"] is not None
        assert data["timestamp_utc"] is not None
        assert data["monotonic_ms"] is not None
        assert data["t_seconds"] is not None
        assert data["status"] == "running"
        # Raw 层（不可变原始量）
        assert data["voltage_raw_v"] is not None
        assert data["current_raw_a"] is not None
        assert data["temperature_raw_c"] is not None
        # Calibrated / Derived 层
        assert data["conductance_s"] is not None
        assert data["kappa_t_us_cm"] is not None
        assert data["kappa_25_us_cm"] is not None
        # Configuration / Trace / Quality
        assert data["excitation_frequency_hz"] == 1000
        assert data["excitation_amplitude_v"] == 0.4
        assert data["range_id"] == "R_100R_10K"
        assert data["sensor_path_id"] == "EC_IV_CELL_01"
        assert data["calibration_id"] == "CAL_TEST_001"
        assert data["cell_constant_cm_inv"] == 1.0
        assert "SIMULATED" in data["quality_flags"]
        # 在线 V2 禁止携带 V1 别名；旧数据兼容仅存在于历史读取层。
        assert "ec" not in data
        assert "temperature" not in data
        assert "timestamp" not in data
        client.post("/api/experiment/stop")
    client.post("/api/experiment/reset")


def test_ws_uncalibrated_frame_remains_a_measurement(client):
    """显式 null 强制未校准；帧仍带 Raw 数据和测量类型，前端不得当成状态帧。"""
    with client.websocket_connect("/ws/stream") as ws:
        response = client.post(
            "/api/experiment/start",
            json={"cell_constant_cm_inv": None, "alpha_per_c": None},
        )
        assert response.status_code == 200
        frame = None
        for _ in range(10):
            message = ws.receive_json()
            if message.get("message_type") == "measurement":
                frame = message
                break
        assert frame is not None
        assert frame["voltage_raw_v"] is not None
        assert frame["current_raw_a"] is not None
        assert frame["conductance_s"] is not None
        assert frame["kappa_t_us_cm"] is None
        assert frame["kappa_25_us_cm"] is None
        assert "ec" not in frame
        assert "temperature" not in frame
        assert "UNCALIBRATED" in frame["quality_flags"]
        client.post("/api/experiment/stop")
    client.post("/api/experiment/reset")


def test_default_mock_calibration_is_traceable(client):
    with client.websocket_connect("/ws/stream") as ws:
        client.post("/api/experiment/start")
        frame = None
        for _ in range(10):
            message = ws.receive_json()
            if message.get("message_type") == "measurement":
                frame = message
                break
        assert frame is not None
        assert frame["calibration_id"] == "CAL_MOCK_CONFIG"
        assert frame["experiment_uid"].startswith("EXP-")
        client.post("/api/experiment/stop")
    client.post("/api/experiment/reset")


@pytest.mark.parametrize(
    "payload",
    [
        {"cell_constant_cm_inv": 0},
        {"cell_constant_cm_inv": -1},
        {"cell_constant_cm_inv": 1.0},
        {"calibration_id": None},
        {"alpha_per_c": 0.1},
        {"excitation_frequency_hz": 0},
        {"excitation_amplitude_v": 0},
    ],
)
def test_start_rejects_invalid_calibration_ranges(client, payload):
    assert client.post("/api/experiment/start", json=payload).status_code == 422


def test_ws_stop_stops_stream(client):
    """停止后：收到 stopped 状态广播，且 300ms 内不再有数据帧。"""
    with client.websocket_connect("/ws/stream") as ws:
        client.post("/api/experiment/start", json=CALIBRATED_START)
        for _ in range(10):
            if _has_measure_data(ws.receive_json()):
                break
        client.post("/api/experiment/stop")
        status_frame = _receive_json_with_timeout(ws, 0.5)
        assert status_frame is not None
        assert not _has_measure_data(status_frame)
        assert status_frame["status"] == "stopped"
        assert _receive_json_with_timeout(ws, 0.3) is None
    client.post("/api/experiment/reset")


def test_debug_bad_frame(client):
    """bad-frame 注入一条看似完整的 V1 帧；前端必须拒绝在线降级。"""
    with client.websocket_connect("/ws/stream") as ws:
        client.post("/api/experiment/start", json=CALIBRATED_START)
        for _ in range(10):
            if _has_measure_data(ws.receive_json()):
                break
        r = client.post("/api/debug/bad-frame")
        assert r.json()["ok"] is True
        bad = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg.get("ec") == 1413.0 and "message_type" not in msg:
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
    """运行期间帧落库；export.csv 与 export.json 可取回，CSV 含 Raw/Calibrated/Derived。"""
    with client.websocket_connect("/ws/stream") as ws:
        exp_id = client.post(
            "/api/experiment/start",
            json={**CALIBRATED_START, "sample_id": "BLANK", "sensor_path_id": "EC_IV_CELL_02"},
        ).json()["experiment_id"]
        got = 0
        for _ in range(15):
            msg = ws.receive_json()
            if _has_measure_data(msg):
                got += 1
            if got >= 3:
                break
        client.post("/api/experiment/stop")
    client.post("/api/experiment/reset")

    # 帧先入队后广播，由后台 drain 攒批落库：轮询等待，避免 stop 后立即读取的竞态
    detail = None
    for _ in range(50):  # 最多约 1s
        detail = client.get(f"/api/experiments/{exp_id}").json()
        if detail["frame_count"] >= 3:
            break
        time.sleep(0.02)
    assert detail["frame_count"] >= 3

    csv_resp = client.get(f"/api/experiments/{exp_id}/export.csv")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    csv_text = csv_resp.text
    # Raw / Calibrated / Derived / Configuration / Trace / Quality 各层字段
    assert "voltage_raw_v" in csv_text
    assert "current_raw_a" in csv_text
    assert "temperature_raw_c" in csv_text
    assert "conductance_s" in csv_text
    assert "kappa_t_us_cm" in csv_text
    assert "kappa_25_us_cm" in csv_text
    assert "legacy_ec_us_cm" in csv_text
    assert "excitation_frequency_hz" in csv_text
    assert "range_id" in csv_text
    assert "calibration_id" in csv_text
    assert "cell_constant_cm_inv" in csv_text
    assert "calibration_valid_until_utc" in csv_text
    assert "EC_IV_CELL_02" in csv_text

    json_resp = client.get(f"/api/experiments/{exp_id}/export.json").json()
    assert len(json_resp["frames"]) >= 3
    assert json_resp["frames"][0]["sensor_path_id"] == "EC_IV_CELL_02"
    # JSON 帧携带原始 U/I/T 与派生量（可追溯回算，原始数据不覆盖）
    frame0 = json_resp["frames"][0]
    assert frame0["voltage_raw_v"] is not None
    assert frame0["current_raw_a"] is not None
    assert frame0["temperature_raw_c"] is not None
    assert frame0["kappa_t_us_cm"] is not None
    assert frame0["kappa_25_us_cm"] is not None
    assert frame0["calibration_id"] == "CAL_TEST_001"
    assert frame0["cell_constant_cm_inv"] == 1.0


def test_experiment_404(client):
    assert client.get("/api/experiments/999999").status_code == 404
    assert client.get("/api/experiments/999999/export.csv").status_code == 404


def test_frames_pagination_is_validated(client):
    assert client.get("/api/experiments/1/frames?limit=0").status_code == 422
    assert client.get("/api/experiments/1/frames?limit=100001").status_code == 422
    assert client.get("/api/experiments/1/frames?offset=-1").status_code == 422


def test_debug_endpoints_are_disabled_without_opt_in(client, monkeypatch):
    monkeypatch.delenv("EC_ENABLE_DEBUG_ENDPOINTS", raising=False)
    assert client.post("/api/debug/bad-frame").status_code == 404
    assert client.post("/api/debug/close-connections").status_code == 404
    assert client.post("/api/debug/burst?count=1").status_code == 404


def test_debug_burst_count_is_bounded(client):
    assert client.post("/api/debug/burst?count=0").status_code == 422
    assert client.post("/api/debug/burst?count=10001").status_code == 422


def test_debug_frame_has_v2_trace_fields():
    from app.stream import generate_frame

    frame = generate_frame(0.1)
    assert frame["message_type"] == "measurement"
    assert frame["seq_no"] == 2
    assert frame["timestamp_utc"].endswith("Z")
    assert isinstance(frame["monotonic_ms"], int)
    assert frame["t_seconds"] == 0.1
    assert frame["voltage_raw_v"] is not None
    assert frame["current_raw_a"] is not None
    assert frame["temperature_raw_c"] is not None
    assert frame["conductance_s"] is not None
    assert frame["kappa_t_us_cm"] is not None
    assert frame["kappa_25_us_cm"] is not None
    assert frame["calibration_id"] is not None
    assert isinstance(frame["quality_flags"], list)
    assert "ec" not in frame
    assert "temperature" not in frame
    assert "timestamp" not in frame


# ---------------- Review B-3 / B-4 / B-5 回归 ----------------

def test_stop_idempotent_does_not_refresh_ended_at(client):
    """B-4：重复 stop 返回 ok=true，但不再刷新 ended_at_utc。"""
    exp_id = client.post("/api/experiment/start").json()["experiment_id"]
    assert client.post("/api/experiment/stop").json()["ok"] is True

    first = client.get(f"/api/experiments/{exp_id}").json()
    assert first["status"] == "stopped"
    assert first["ended_at_utc"] is not None

    r2 = client.post("/api/experiment/stop")
    assert r2.json()["ok"] is True
    assert r2.json()["message"] == "当前没有运行中的实验"

    second = client.get(f"/api/experiments/{exp_id}").json()
    assert second["status"] == "stopped"
    assert second["ended_at_utc"] == first["ended_at_utc"]
    client.post("/api/experiment/reset")


def test_reset_running_marks_aborted(client):
    """B-5：running 中 reset → 历史记录 status 为 aborted（而非 idle）。"""
    exp_id = client.post("/api/experiment/start").json()["experiment_id"]
    client.post("/api/experiment/reset")

    detail = client.get(f"/api/experiments/{exp_id}").json()
    assert detail["status"] == "aborted"
    assert detail["ended_at_utc"] is not None

    # 内存态回 idle，可再次 start
    assert client.post("/api/experiment/start").json()["status"] == "running"
    client.post("/api/experiment/reset")


def test_burst_broadcasts_only_no_persist(client):
    """B-3：/api/debug/burst 仅广播、不落库（注入帧不污染 raw_frames）。"""
    with client.websocket_connect("/ws/stream") as ws:
        exp_id = client.post("/api/experiment/start").json()["experiment_id"]
        # 等到第一条真实采集帧，确认流已建立
        for _ in range(10):
            if _has_measure_data(ws.receive_json()):
                break
        r = client.post("/api/debug/burst?count=20")
        assert r.json()["ok"] is True
        assert r.json()["sent"] == 20
        client.post("/api/experiment/stop")
    client.post("/api/experiment/reset")

    detail = client.get(f"/api/experiments/{exp_id}").json()
    # 注入的 20 帧不得落库：frame_count 远小于注入量，仅含采集循环真实帧
    assert detail["frame_count"] < 20
