"""2026-09-30 全面审查修复轮回归测试。

- #1 QC：COMPUTE_INVALID 帧上的硬标志与无效帧占比必须参与判稳
  （simulator voltage_oor 实测：49 帧中 24 帧 OUT_OF_RANGE，旧实现判 PASS）
- #1 附带：update_sample_qc 覆写代表值——续跑后判 FAIL 不得残留上一次 PASS 的代表值
- #3 数据帧带 experiment_id、running/stopped 状态帧带 sample_id（前端按实验隔离缓冲的依据）
- #4 跨实验浓度标定：列表带主样品摘要、/api/analysis/calibration 取点/准入/报告、
  Kohlrausch 接受 c=0 空白样
"""

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app import analysis, storage
from app.main import app
from app.stability import qc_from_frames

SENSOR = "MOCK_EC_IV"


def _row(k25, flags="SIMULATED", t=0.0):
    return {"kappa_25_us_cm": k25, "quality_flags": flags, "t_seconds": t}


def _stable_rows(n, start_t=0.0):
    return [_row(1413.0 + (0.1 if i % 2 else -0.1), t=start_t + i * 0.1) for i in range(n)]


# ---------------- #1 QC ----------------


def test_qc_hard_flag_on_compute_invalid_frames_fails():
    """电压越界隔帧注入：硬标志只出现在被计算链拒绝的帧上，也必须判 FAIL。"""
    rows = []
    for i in range(60):
        if i % 2:
            rows.append(_row(None, "SIMULATED|OUT_OF_RANGE|VOLTAGE_OOR|COMPUTE_INVALID", i * 0.1))
        else:
            rows.append(_row(1413.0 + (0.1 if i % 4 else -0.1), t=i * 0.1))
    result = qc_from_frames(rows)
    assert (result.status, result.reason) == ("FAIL", "hard_quality_flag")
    assert result.representative_value is None


def test_qc_trailing_invalid_frames_fail_without_hard_flag():
    """末尾电极脱开（U≤0 全部 COMPUTE_INVALID、无硬标志）：旧实现拿更早的稳定段判 PASS。"""
    rows = _stable_rows(40) + [
        _row(None, "SIMULATED|COMPUTE_INVALID", 4.0 + i * 0.1) for i in range(20)
    ]
    result = qc_from_frames(rows)
    assert (result.status, result.reason) == ("FAIL", "invalid_frames")


def test_qc_single_invalid_frame_downgrades_pass_to_warn():
    rows = _stable_rows(40)
    rows[35] = _row(None, "SIMULATED|COMPUTE_INVALID", rows[35]["t_seconds"])
    result = qc_from_frames(rows)
    assert (result.status, result.reason) == ("WARN", "some_invalid_frames")
    assert result.representative_value is None
    assert result.median is not None


def test_qc_edge_cases_keep_old_semantics():
    assert qc_from_frames([_row(None, "COMPUTE_INVALID", i * 0.1) for i in range(10)]).reason == (
        "invalid_frames"
    )
    clean = qc_from_frames(_stable_rows(40))
    assert clean.status == "PASS" and clean.representative_value is not None
    # 帧不足 3 条 / 从未算过 κ25 的旧 V1 帧：不写 QC（与旧行为一致）
    assert qc_from_frames(_stable_rows(2)) is None
    assert qc_from_frames([{"kappa_25_us_cm": None, "quality_flags": "SIMULATED"}] * 5) is None


def test_simulator_voltage_oor_experiment_qc_fails(tmp_path, monkeypatch):
    """端到端复现审查 #1：显式 voltage_oor 故障跑一轮，停止后 QC 必须是 FAIL。"""
    cfg = tmp_path / "sim.json"
    cfg.write_text(
        json.dumps(
            {
                "driver": "simulator",
                "mode": "stable",
                "fault_kind": "voltage_oor",
                "fault_start_s": 0.0,
                "sample_rate_hz": 20,
                "sweep_seconds": 0.0,
                "settle_seconds": 0.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EC_DRIVER", "simulator")
    monkeypatch.setenv("EC_SIM_CONFIG", str(cfg))
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "oor.db"))
    with TestClient(app) as c:
        exp_id = c.post("/api/experiment/start").json()["experiment_id"]
        time.sleep(1.2)
        c.post("/api/experiment/stop")
        sample = storage.get_samples(exp_id)[0]
    assert sample["qc_status"] == "FAIL"
    assert sample["qc_reason"] == "hard_quality_flag"
    assert sample["representative_value"] is None


def test_update_sample_qc_overwrites_stale_representative(tmp_path, monkeypatch):
    """续跑后第二次停止判 FAIL：不得留着第一次 PASS 的代表值（界面会显示 FAIL + 代表值）。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "qc.db"))
    storage.init_db()
    exp = storage.create_experiment_with_sample(
        experiment_id="EXP-QC", title="t", sample_id="S", sensor_path_id=SENSOR
    )
    common = {"experiment_id": exp, "sample_id": "S", "sensor_path_id": SENSOR}
    storage.update_sample_qc(
        **common, qc_status="PASS", qc_reason="stable", representative_value=1413.0,
        k25_median=1413.0, k25_mean=1413.0, k25_sd=0.1,
    )
    storage.update_sample_qc(
        **common, qc_status="FAIL", qc_reason="hard_quality_flag", representative_value=None,
        k25_median=1500.0, k25_mean=1499.0, k25_sd=9.0,
    )
    s = storage.get_samples(exp)[0]
    assert s["qc_status"] == "FAIL"
    assert s["representative_value"] is None
    assert s["k25_median"] == 1500.0


# ---------------- #3 实验隔离所需的协议字段 ----------------


def test_frames_carry_experiment_id_and_status_frames_carry_sample_id(client):
    with client.websocket_connect("/ws/stream") as ws:
        exp_id = client.post("/api/experiment/start", json={"sample_id": "OBS_B"}).json()[
            "experiment_id"
        ]
        running = frame = None
        for _ in range(20):
            msg = ws.receive_json()
            if "ec" in msg:
                frame = msg
                break
            if msg.get("status") == "running":
                running = msg
        assert running == {"status": "running", "experiment_id": exp_id, "sample_id": "OBS_B"}
        assert frame is not None and frame["experiment_id"] == exp_id

        client.post("/api/experiment/stop")
        stopped = None
        for _ in range(50):
            msg = ws.receive_json()
            if "ec" not in msg and msg.get("status") == "stopped":
                stopped = msg
                break
        assert stopped is not None
        assert (stopped["experiment_id"], stopped["sample_id"]) == (exp_id, "OBS_B")
    client.post("/api/experiment/reset")


# ---------------- #4 跨实验浓度标定 ----------------


def _stopped_experiment(uid, conc, *, qc="PASS", value=1413.0, status="stopped"):
    exp = storage.create_experiment_with_sample(
        experiment_id=uid, title="t", sample_id=uid, sensor_path_id=SENSOR,
        concentration_mmol_l=conc,
    )
    if qc is not None:
        storage.update_sample_qc(
            experiment_id=exp, sample_id=uid, sensor_path_id=SENSOR, qc_status=qc,
            qc_reason="test", representative_value=value if qc == "PASS" else None,
            k25_median=value, k25_mean=value, k25_sd=0.1,
        )
    if status != "running":
        storage.finish_experiment(exp, status)
    return exp


def test_list_experiments_includes_primary_sample_summary(client):
    exp = _stopped_experiment("LIST-1", 10.0, value=1234.5)
    item = next(e for e in client.get("/api/experiments").json() if e["id"] == exp)
    assert item["concentration_mmol_l"] == 10.0
    assert item["qc_status"] == "PASS"
    assert item["representative_value"] == 1234.5
    assert item["k25_median"] == 1234.5
    assert item["frame_count"] == 0


def test_kohlrausch_accepts_blank_sample_and_rejects_negative():
    c = [0.0, 1.0, 2.0, 4.0, 8.0]
    res = analysis.fit_kohlrausch(c, [15 + 95 * ci - 8 * ci**1.5 for ci in c])
    assert res is not None
    assert abs(res["params"]["a"] - 15.0) < 1e-6  # κblank 由 c=0 空白样锚定
    assert analysis.fit_kohlrausch([-1.0, 1.0, 2.0], [1.0, 2.0, 3.0]) is None


def test_calibration_fits_one_point_per_experiment(client, tmp_path):
    kohl = lambda c: 15 + 95 * c - 8 * c**1.5  # noqa: E731  κblank=15、Λ0=95、K=8
    conc = [0.0, 1.0, 2.0, 4.0, 8.0]
    ids = [_stopped_experiment(f"CAL-{i}", c, value=kohl(c)) for i, c in enumerate(conc)]
    # WARN 实验没有代表值，取窗口中位数，来源如实标注
    ids.append(_stopped_experiment("CAL-WARN", 16.0, qc="WARN", value=kohl(16.0)))

    r = client.post("/api/analysis/calibration", json={"experiment_ids": ids})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["experiment_id"] for p in body["points"]] == ids
    assert [p["source"] for p in body["points"]] == ["representative"] * 5 + ["median"]
    fit = next(m for m in body["models"] if m["model"] == "kohlrausch")
    assert abs(fit["params"]["a"] - 15.0) < 1e-6
    assert abs(fit["params"]["b"] - 95.0) < 1e-6
    assert abs(fit["params"]["K"] - 8.0) < 1e-6

    path = Path(body["derived_path"])
    assert path.parent == tmp_path / "derived"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["kind"] == "calibration"
    assert report["experiment_ids"] == ids
    # 同一组实验（顺序无关）覆写同一份报告
    again = client.post("/api/analysis/calibration", json={"experiment_ids": ids[::-1]})
    assert again.json()["derived_path"] == body["derived_path"]


def test_calibration_rejects_ineligible_selection(client):
    ok = [_stopped_experiment(f"V-{c}", c) for c in (0.0, 5.0, 10.0)]
    bad_cases = (
        (_stopped_experiment("V-FAIL", 20.0, qc="FAIL"), "QC 为 FAIL"),
        (_stopped_experiment("V-NOQC", 25.0, qc=None), "QC 为 未判定"),
        (_stopped_experiment("V-NOC", None), "未填写浓度"),
        (_stopped_experiment("V-RUN", 30.0, status="running"), "须为已停止"),
        (999_999, "不存在"),
    )
    for bad_id, needle in bad_cases:
        r = client.post("/api/analysis/calibration", json={"experiment_ids": ok + [bad_id]})
        assert r.status_code == 400, (needle, r.text)
        assert needle in r.json()["detail"]

    same = [_stopped_experiment(f"V-SAME-{i}", 5.0) for i in range(3)]
    r = client.post("/api/analysis/calibration", json={"experiment_ids": same})
    assert r.status_code == 400 and "3 个不同浓度" in r.json()["detail"]
    assert client.post("/api/analysis/calibration", json={"experiment_ids": ok[:2]}).status_code == 422
