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


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["persistence"] == "ok"
    # 即使挂了 frontend/dist，API 路径也不能被静态文件吃掉
    assert r.headers.get("content-type", "").startswith("application/json")


def test_current_experiment_and_default_sensor_path(client):
    idle = client.get("/api/experiment/current").json()
    assert idle["status"] == "idle"
    assert idle["experiment_id"] is None

    started = client.post("/api/experiment/start").json()
    current = client.get("/api/experiment/current").json()
    assert current["status"] == "running"
    assert current["experiment_id"] == started["experiment_id"]
    detail = client.get(f"/api/experiments/{started['experiment_id']}").json()
    assert detail["sensor_path_id"] == "MOCK_EC_IV"

    client.post("/api/experiment/stop")
    stopped = client.get("/api/experiment/current").json()
    assert stopped["status"] == "stopped"
    assert stopped["experiment_id"] == started["experiment_id"]
    client.post("/api/experiment/reset")
    assert client.get("/api/experiment/current").json()["experiment_id"] is None


def test_start_after_stop_resumes_same_experiment(client):
    first = client.post("/api/experiment/start", json={"sample_id": "BLANK"}).json()
    exp_id = first["experiment_id"]
    client.post("/api/experiment/stop")
    again = client.post("/api/experiment/start", json={"sample_id": "BLANK"}).json()
    assert again["ok"] is True
    assert again["resumed"] is True
    assert again["experiment_id"] == exp_id
    assert client.get("/api/experiment/current").json()["status"] == "running"
    detail = client.get(f"/api/experiments/{exp_id}").json()
    assert detail["status"] == "running"
    assert detail["ended_at_utc"] is None
    client.post("/api/experiment/reset")


def test_new_sample_after_stop_starts_new_experiment(client):
    first = client.post("/api/experiment/start", json={"sample_id": "BLANK"}).json()["experiment_id"]
    client.post("/api/experiment/stop")
    second = client.post("/api/experiment/start", json={"sample_id": "NACL_004"}).json()
    assert second["ok"] is True
    assert second["resumed"] is False
    assert second["experiment_id"] != first
    assert client.get(f"/api/experiments/{first}").json()["status"] == "stopped"
    client.post("/api/experiment/reset")


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
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["message"] == "实验已在进行中"

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
        # 兼容层：旧 4 字段必须仍在（旧前端不破）
        assert {"timestamp", "ec", "temperature", "status"} <= set(data)
        # I–V 扩展字段（REQ-M-001 软件侧）：原始量 + 计算链结果 + 溯源
        assert {
            "schema_version",
            "device_id",
            "firmware_version",
            "range_id",
            "voltage_raw_v",
            "current_raw_a",
            "temperature_raw_c",
            "conductance_s",
            "kappa_t_us_cm",
            "kappa_25_us_cm",
            "quality_flags",
            "calibration_id",
            "excitation_frequency_hz",
            "excitation_amplitude_v",
            "compensation_model",
        } <= set(data)
        assert data["calibration_id"] == "MOCK-KCELL-1.0"
        assert data["compensation_model"] == "linear_alpha"
        assert data["excitation_frequency_hz"] == 1000.0
        assert data["status"] == "running"
        assert data["ec"] is not None
        assert data["kappa_25_us_cm"] is not None
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
    assert "ec_raw_us_cm" in csv_text
    assert "BA121S_LOW" in csv_text
    # I–V 列（v2 起）：表头与数据行都必须包含电压/电流/电导/κ(T)/κ25
    header = csv_text.splitlines()[0].split(",")
    for col in ("voltage_raw_v", "current_raw_a", "conductance_s", "kappa_t_us_cm", "kappa_25_us_cm"):
        assert col in header, f"表头缺 {col}"
        idx = header.index(col)
        values = [ln.split(",")[idx] for ln in csv_text.splitlines()[1:] if ln.strip()]
        assert any(v not in ("", "None") for v in values), f"{col} 列全空"

    json_resp = client.get(f"/api/experiments/{exp_id}/export.json")
    assert json_resp.status_code == 200
    assert "attachment" in json_resp.headers.get("content-disposition", "")
    body = json_resp.json()
    assert len(body["frames"]) >= 3
    assert body["frames"][0]["sensor_path_id"] == "BA121S_LOW"
    assert body["truncated"] is False
    assert body["frame_count_total"] == len(body["frames"])


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


# ---------------- 开始实验：浓度字段 ----------------

def test_start_persists_concentration(client):
    """开始实验时的浓度写入 samples，停止后结果区可从详情读取。"""
    started = client.post(
        "/api/experiment/start",
        json={"sample_id": "NACL_010", "concentration_mmol_l": 10},
    ).json()
    exp_id = started["experiment_id"]
    detail = client.get(f"/api/experiments/{exp_id}").json()
    assert detail["samples"][0]["sample_id"] == "NACL_010"
    assert detail["samples"][0]["concentration_mmol_l"] == 10
    client.post("/api/experiment/reset")


def test_start_writes_calibration_and_frame_metadata(client):
    """v5：开始实验写入 calibration_records，帧落库带 calibration_id 与协议元数据。"""
    import time as _time

    exp_id = client.post("/api/experiment/start").json()["experiment_id"]
    _time.sleep(0.4)
    client.post("/api/experiment/stop")

    detail = client.get(f"/api/experiments/{exp_id}").json()
    assert detail["calibrations"]
    cal = detail["calibrations"][0]
    assert cal["calibration_id"] == "MOCK-KCELL-1.0"
    assert cal["lot"] == "SIMULATED"
    assert "KCl 1413" in (cal["standard"] or "")
    assert "(simulated)" in (cal["standard"] or "")
    assert cal["coeff_value"] == 1.0

    frames = client.get(f"/api/experiments/{exp_id}/frames").json()["frames"]
    assert frames
    frame = frames[0]
    assert frame["calibration_id"] == "MOCK-KCELL-1.0"
    assert frame["device_id"] == "MOCK-IV-01"
    assert frame["firmware_version"] == "0.1.0"
    assert frame["range_id"] == "WIDE"
    assert frame["compensation_model"] == "linear_alpha"
    assert frame["schema_version"] == 2
    assert frame["kappa_25_us_cm"] is not None

    header = client.get(f"/api/experiments/{exp_id}/export.csv").text.splitlines()[0]
    assert "kappa_25_us_cm" in header
    assert "calibration_id" in header
    client.post("/api/experiment/reset")


def test_start_rejects_negative_concentration(client):
    r = client.post("/api/experiment/start", json={"concentration_mmol_l": -1})
    assert r.status_code == 422
    idle = client.get("/api/experiment/current").json()
    assert idle["status"] == "idle"


# ---------------- 判稳与 QC（REQ-D-003） ----------------

def test_stop_writes_qc_to_samples(client):
    """实验停止时自动判稳，QC 结果（状态/原因/代表值/统计量）写入 samples。"""
    import time as _time

    exp_id = client.post("/api/experiment/start").json()["experiment_id"]
    # 等够 ≥ min_samples(10) 帧（默认 10Hz）：约 1.6s → ~16 帧，稳定场景应判 PASS
    _time.sleep(1.6)
    client.post("/api/experiment/stop")

    detail = client.get(f"/api/experiments/{exp_id}").json()
    samples = detail["samples"]
    assert len(samples) == 1
    s = samples[0]
    assert s["qc_status"] in {"PASS", "WARN", "FAIL"}
    assert s["qc_reason"]
    assert s["qc_checked_at_utc"] is not None
    # 帧数足够 + mock 稳定场景：应判 PASS，代表值非空
    assert s["qc_status"] == "PASS"
    assert s["representative_value"] is not None
    assert s["k25_median"] is not None
    assert s["k25_mean"] is not None
    client.post("/api/experiment/reset")


def test_short_experiment_skips_qc(client):
    """帧不足（<3）时停止：不写 QC，不影响 stop 主流程。"""
    import time as _time

    exp_id = client.post("/api/experiment/start").json()["experiment_id"]
    _time.sleep(0.1)  # 可能不足 3 帧
    client.post("/api/experiment/stop")
    detail = client.get(f"/api/experiments/{exp_id}").json()
    s = detail["samples"][0]
    # 允许帧不足跳过（qc_status 为 NULL），但不允许 stop 报错
    assert detail["status"] == "stopped"
    if detail["frame_count"] < 3:
        assert s["qc_status"] is None
    client.post("/api/experiment/reset")


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
            if "ec" in ws.receive_json():
                break
        r = client.post("/api/debug/burst?count=20")
        assert r.json()["ok"] is True
        assert r.json()["sent"] == 20
        client.post("/api/experiment/stop")
    client.post("/api/experiment/reset")

    detail = client.get(f"/api/experiments/{exp_id}").json()
    # 注入的 20 帧不得落库：frame_count 远小于注入量，仅含采集循环真实帧
    assert detail["frame_count"] < 20
