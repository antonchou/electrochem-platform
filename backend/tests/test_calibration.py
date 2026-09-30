"""跨实验浓度标定：列表的主样品摘要、/api/analysis/calibration 的取点、准入与报告。"""

import json
from pathlib import Path

from app import storage

SENSOR = "MOCK_EC_IV"


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
