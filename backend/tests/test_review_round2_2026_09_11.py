"""2026-09-11 未提交修复轮复审（第二轮）回归测试。

覆盖复审报告 P1-4 / P1-5 / P2-4 / P2-6（P1-1~P1-3、P2-1/2/3/11 为前端或
部署脚本改动，分别由前端 node:test 单测与 bash -n / 人工验证覆盖）。

- P1-4 空拟合结果不得清空同轴既有 fit_results / 覆写 derived 报告
- P1-5 退化判据改相对口径：小量级真实信号（y~1e-6）不被误杀
- P2-4 insert_frames 丢弃畸形帧必须留 warning 日志
- P2-6 check_stability 的 timestamps 错配必须显式报错
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app import analysis, storage
from app.main import app
from app.stability import StabilityConfig, check_stability


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "round2.db"))
    with TestClient(app) as c:
        yield c


def _start(client) -> int:
    r = client.post("/api/experiment/start", json={"sample_id": "R2"})
    assert r.json()["ok"], r.text
    return r.json()["experiment_id"]


def test_empty_fit_result_keeps_existing_fit_results(client):
    """P1-4：先有效拟合入库，再提交退化数据（空结果）——既有记录必须保留。"""
    exp_id = _start(client)

    good = {
        "x": [0.0, 1.0, 2.0, 3.0, 4.0],
        "y": [100.0, 102.0, 104.0, 106.0, 108.0],
        "models": ["linear"],
        "x_axis": "time",
        "experiment_id": exp_id,
    }
    r1 = client.post("/api/analysis/fit", json=good)
    assert r1.status_code == 200, r1.text
    assert r1.json()["models"], "有效数据必须产出拟合结果"
    assert storage.get_fit_results(exp_id), "有效拟合应写入 fit_results"

    # 退化数据（y 恒定，P1-5 后稳定返回空列表）
    degenerate = {**good, "y": [105.0] * 5}
    r2 = client.post("/api/analysis/fit", json=degenerate)
    assert r2.status_code == 200
    assert r2.json()["models"] == []

    # 修复前：空结果触发 DELETE + 空插入 → 既有记录被清空
    assert storage.get_fit_results(exp_id), "空拟合结果不得清空既有 fit_results"


def test_small_magnitude_signal_fits_on_all_models(client):
    """P1-5：y~1e-6 且带真实斜率，全模型不被退化判据误杀（复审 §3.5 case 4）。"""
    x = [float(i) for i in range(30)]
    y = [1e-6 + 1e-9 * xi for xi in x]
    r = client.post(
        "/api/analysis/fit",
        json={"x": x, "y": y, "x_axis": "time"},
    )
    assert r.status_code == 200
    models = r.json()["models"]
    assert models, "小量级真实信号不得被判退化"
    best = models[0]
    assert best["r2"] > 0.99


def test_fit_all_constant_y_still_returns_empty():
    """P1-5 反向：真正的常数 y 仍必须判退化（相对口径下 ss_tot=0 命中）。"""
    assert analysis.fit_all(list(range(20)), [1413.0] * 20, ["linear"], "time") == []


def test_check_stability_rejects_mismatched_timestamps():
    """P2-6：timestamps 与 values 不等长必须 ValueError，不得静默截断算斜率。"""
    values = [1400.0 + i for i in range(10)]
    with pytest.raises(ValueError, match="timestamps"):
        check_stability(
            values,
            timestamps=[float(i) for i in range(5)],
            config=StabilityConfig(),
        )


def test_insert_frames_malformed_drop_is_logged(tmp_path, monkeypatch, caplog):
    """P2-4：畸形帧被丢弃时必须留 warning（带条数），不得静默消失。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "r2-log.db"))
    storage.init_db()
    exp_id = storage.create_experiment("EXP-R2", "logging")
    good = {
        "experiment_id": exp_id,
        "sample_id": "S1",
        "sensor_path_id": "MOCK_EC_IV",
        "t_seconds": 0.1,
        "ec_raw": 100.0,
        "temperature_raw": 25.0,
    }
    malformed = {k: v for k, v in good.items() if k != "temperature_raw"}

    with caplog.at_level(logging.WARNING, logger="app.storage"):
        storage.insert_frames([good, malformed, dict(malformed)])

    assert any(
        "skipped 2 malformed frame" in rec.message for rec in caplog.records
    ), [r.getMessage() for r in caplog.records]
    assert len(storage.get_frames(exp_id)) == 1
