import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import FakeDevice
from ec.app import create_app
from ec.chemistry import kcl_kappa25_us_cm
from ec.devices import SimulatedCell
from ec.qc import QcConfig
from ec.settings import Settings

TRUE_KCELL = 1.02


@pytest.fixture
def api_settings(tmp_path):
    return Settings(db_path=tmp_path / "ec.db", static_dir=tmp_path / "dist", qc=QcConfig(window_s=0.5, min_points=5))


@pytest.fixture
def client(api_settings):
    device = SimulatedCell(rate_hz=40.0, seed=1, settle_s=0.02, cell_constant_per_cm=TRUE_KCELL)
    with TestClient(create_app(api_settings, device)) as c:
        wait_for(lambda: c.get("/api/state").json()["device"]["connected"])
        yield c


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def measure(client, sample_name, concentration=None, seconds=0.8):
    response = client.post("/api/measurements", json={"sample_name": sample_name, "concentration_mmol_l": concentration})
    assert response.status_code == 201, response.text
    time.sleep(seconds)
    stopped = client.post("/api/measurements/current/stop")
    assert stopped.status_code == 200, stopped.text
    return stopped.json()


def test_state_and_standards(client):
    state = client.get("/api/state").json()
    assert state["device"]["kind"] == "sim" and state["device"]["info"]["device_id"] == "SIM-IV-01"
    assert state["measurement"] is None and state["calibration"] is None
    assert state["cell_constant_per_cm"] == 1.0
    names = [s["name"] for s in client.get("/api/standards").json()]
    assert "KCl 0.01 mol/L" in names


@pytest.mark.parametrize(
    "body",
    [
        {"sample_name": ""},
        {"sample_name": "   "},
        {"sample_name": "x", "concentration_mmol_l": -1},
        {"sample_name": "x", "unexpected": 1},
        {},
    ],
)
def test_start_validation(client, body):
    assert client.post("/api/measurements", json=body).status_code == 422


def test_nonfinite_concentration_rejected(client):
    # httpx 不肯编码 NaN，直接发原始 JSON 文本（浏览器/脚本可能这样发）
    body = '{"sample_name": "x", "concentration_mmol_l": NaN}'
    response = client.post("/api/measurements", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 422


def test_measurement_flow(client):
    started = client.post("/api/measurements", json={"sample_name": " KCl 10 mM ", "concentration_mmol_l": 10})
    assert started.status_code == 201
    m = started.json()
    assert m["sample_name"] == "KCl 10 mM" and m["status"] == "running"
    assert client.post("/api/measurements", json={"sample_name": "again"}).status_code == 409
    time.sleep(0.8)
    finished = client.post("/api/measurements/current/stop").json()
    assert finished["status"] == "completed" and finished["frame_count"] >= 20
    assert finished["qc"]["verdict"] == "PASS", finished["qc"]
    # 未标定：读数 = 真值 / 真 Kcell
    expected = (kcl_kappa25_us_cm(10) + 1.5) / TRUE_KCELL
    assert finished["qc"]["representative_kappa25"] == pytest.approx(expected, rel=0.005)
    assert client.post("/api/measurements/current/stop").status_code == 409

    listing = client.get("/api/measurements").json()
    assert [row["id"] for row in listing] == [m["id"]]
    assert client.get(f"/api/measurements/{m['id']}").json()["qc"]["verdict"] == "PASS"

    points = client.get(f"/api/measurements/{m['id']}/points", params={"max_points": 10}).json()
    assert points["total"] == finished["frame_count"]
    assert len(points["points"]) <= 11 and points["points"][-1]["seq"] == points["total"]

    csv = client.get(f"/api/measurements/{m['id']}/frames.csv")
    assert csv.headers["content-disposition"] == f'attachment; filename="measurement_{m["id"]}.csv"'
    lines = csv.text.strip().split("\n")
    assert lines[0].startswith("seq,t_s,timestamp_utc,device_seq")
    assert len(lines) == finished["frame_count"] + 1

    fit = client.get(f"/api/measurements/{m['id']}/temperature-fit").json()
    assert fit["ok"] is False  # 恒温测量无法估计 α


def test_unknown_measurement_is_404(client):
    assert client.get("/api/measurements/999").status_code == 404
    assert client.get("/api/measurements/999/frames.csv").status_code == 404


def test_websocket_snapshot_then_ordered_updates(client):
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "snapshot" and first["state"]["measurement"] is None
        m = client.post("/api/measurements", json={"sample_name": "ws"}).json()
        seen_state = False
        for _ in range(200):
            message = ws.receive_json()
            if message["type"] == "state" and message["state"]["measurement"]:
                assert message["state"]["measurement"]["id"] == m["id"]
                seen_state = True
            if message["type"] == "reading" and message["measurement_id"] == m["id"]:
                assert seen_state, "状态消息必须先于该测量的读数"
                assert message["point"]["seq"] == 1
                break
        else:
            raise AssertionError("没收到测量读数")
    client.post("/api/measurements/current/stop")


def test_cross_site_requests_are_rejected(client):
    evil = {"Origin": "http://evil.example"}
    assert client.post("/api/measurements", json={"sample_name": "x"}, headers=evil).status_code == 403
    assert client.post("/api/measurements/current/stop", headers=evil).status_code == 403
    assert client.get("/api/state", headers=evil).status_code == 200  # 只读请求不拦
    ok = client.post("/api/measurements", json={"sample_name": "x"}, headers={"Origin": "http://testserver"})
    assert ok.status_code == 201
    client.post("/api/measurements/current/stop")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers=evil) as ws:
            ws.receive_json()


def test_concentration_analysis(client):
    ids = [measure(client, f"KCl {c} mM", c)["id"] for c in (1.0, 5.0, 10.0)]
    blank = measure(client, "unknown", None)["id"]
    result = client.post("/api/analysis/concentration", json={"measurement_ids": ids}).json()
    assert result["linear"]["ok"] and result["kohlrausch"]["ok"]
    # 未标定时 Λ0 也偏低同一个倍数；读数经过 1.5 µS/cm 水本底，允许 2% 偏差
    assert result["kohlrausch"]["lambda0_s_cm2_per_mol"]["value"] == pytest.approx(149.8 / TRUE_KCELL, rel=0.02)
    missing = client.post("/api/analysis/concentration", json={"measurement_ids": [ids[0], blank]})
    assert missing.status_code == 422 and "没有填浓度" in missing.json()["detail"]
    assert client.post("/api/analysis/concentration", json={"measurement_ids": [ids[0], ids[0]]}).status_code == 422


def test_calibration_round_trip(client):
    standard = measure(client, "KCl 0.01 mol/L", 10.0)
    response = client.post("/api/calibrations", json={
        "points": [{"measurement_id": standard["id"], "standard_name": "KCl 0.01 mol/L", "standard_kappa25_us_cm": 1413.0}],
        "operator": "张三", "lot": "B-2026-09",
    })
    assert response.status_code == 201, response.text
    calibration = response.json()
    assert calibration["cell_constant_per_cm"] == pytest.approx(TRUE_KCELL, rel=0.005)
    assert client.get("/api/state").json()["calibration"]["id"] == calibration["id"]
    after = measure(client, "KCl again", 10.0)
    assert after["calibration_id"] == calibration["id"]
    assert after["qc"]["representative_kappa25"] == pytest.approx(1413.0, rel=0.005)
    listing = client.get("/api/calibrations").json()
    assert listing[0]["points"][0]["sample_name"] == "KCl 0.01 mol/L"
    bad = client.post("/api/calibrations", json={"points": [
        {"measurement_id": 999, "standard_name": "x", "standard_kappa25_us_cm": 1413.0}]})
    assert bad.status_code == 404


def test_simulator_faults_show_up_as_flags(client):
    assert client.post("/api/simulator", json={"fault": "melted"}).status_code == 422
    assert client.post("/api/simulator", json={"fault": "air"}).json() == {"fault": "air"}
    with client.websocket_connect("/ws") as ws:
        for _ in range(50):
            message = ws.receive_json()
            if message["type"] == "reading" and "OPEN_CIRCUIT" in message["point"]["flags"]:
                break
        else:
            raise AssertionError("没看到 OPEN_CIRCUIT")
    client.post("/api/simulator", json={"fault": "none"})


def test_simulator_endpoint_needs_the_simulator(api_settings):
    with TestClient(create_app(api_settings, FakeDevice())) as c:
        assert c.post("/api/simulator", json={"fault": "air"}).status_code == 409


def test_serves_built_frontend(api_settings):
    api_settings.static_dir.mkdir()
    (api_settings.static_dir / "index.html").write_text("<!doctype html><title>EC v2</title>", encoding="utf-8")
    with TestClient(create_app(api_settings, FakeDevice())) as c:
        assert "EC v2" in c.get("/").text
        assert c.get("/api/state").status_code == 200
