"""备选公式拟合模块与 /api/analysis/fit 接口测试。

覆盖：通用多项式/线性化模型（时间轴）、一阶指数饱和、Kohlrausch、
Arrhenius 活化能、x_axis 模型池过滤与接口透传。
"""

import math

from app import analysis, storage


def test_linear_fit_exact():
    """线性数据应精确还原 y = 2 + 3x。"""
    from app import analysis

    x = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    y = [2 + 3 * xi for xi in x]
    res = analysis.fit_linear(x, y)
    assert res is not None
    assert abs(res["params"]["a"] - 2.0) < 1e-6
    assert abs(res["params"]["b"] - 3.0) < 1e-6
    assert res["r2"] > 0.9999
    assert len(res["fitted"]) == analysis.FIT_SAMPLES


def test_quadratic_and_exponential():
    """二次与指数数据应分别由对应模型最优还原。"""
    from app import analysis

    # 二次
    x = [float(i) for i in range(5)]
    y = [1 + 2 * xi + 3 * xi * xi for xi in x]
    q = analysis.fit_quadratic(x, y)
    assert q is not None and q["r2"] > 0.9999
    assert abs(q["params"]["c"] - 3.0) < 1e-6

    # 指数 y = 5·e^(0.4x)
    x2 = [float(i) / 2 for i in range(1, 11)]
    y2 = [5 * math.exp(0.4 * xi) for xi in x2]
    e = analysis.fit_exponential(x2, y2)
    assert e is not None and e["r2"] > 0.999
    assert abs(e["params"]["a"] - 5.0) < 0.05
    assert abs(e["params"]["b"] - 0.4) < 0.02


def test_fit_all_sorted_by_r2():
    """fit_all 应对全模型按 R² 排序；纯线性数据最优为 linear。"""
    from app import analysis

    x = [float(i) for i in range(8)]
    y = [3 + 0.5 * xi for xi in x]  # 纯线性
    results = analysis.fit_all(x, y)
    assert results[0]["model"] == "linear"
    assert all(results[i]["r2"] >= results[i + 1]["r2"] for i in range(len(results) - 1))


def test_fit_requires_positive_domain():
    """对数/幂要求 x>0；指数要求 y>0，违例时返回 None 而不是崩溃。"""
    from app import analysis

    assert analysis.fit_logarithmic([0.0, 1.0, 2.0], [1.0, 2.0, 3.0]) is None
    assert analysis.fit_power([0.0, 1.0, 2.0], [1.0, 2.0, 4.0]) is None
    assert analysis.fit_exponential([0.0, 1.0, 2.0], [0.0, 2.0, 4.0]) is None
    # Kohlrausch 要求 c≥0（c=0 空白样合法，见本文件 test_kohlrausch_accepts_blank_sample_and_rejects_negative）
    assert analysis.fit_kohlrausch([-1.0, 1.0, 2.0], [1.0, 2.0, 3.0]) is None
    # Arrhenius 要求 y>0
    assert analysis.fit_arrhenius([15.0, 20.0, 25.0], [0.0, 1.0, 2.0]) is None


def test_first_order_saturation():
    """一阶指数饱和 y = 50 − 30·e^(−0.5x) 应还原 a≈50、b≈30、k≈0.5。"""
    from app import analysis

    x = [float(i) for i in range(20)]  # 0..19
    y = [50 - 30 * math.exp(-0.5 * xi) for xi in x]
    res = analysis.fit_first_order(x, y)
    assert res is not None
    assert res["r2"] > 0.999
    assert abs(res["params"]["a"] - 50.0) < 0.5
    assert abs(res["params"]["b"] - 30.0) < 2.0
    assert 0.3 < res["params"]["k"] < 0.7
    assert res["label"] == "一阶指数饱和 y = a − b·e^(−k·x)"


def test_kohlrausch_law():
    """Kohlrausch（含截距）：κ = κblank + Λ0·c − K·c^1.5，应还原 a=κblank、b=Λ0、K。"""
    from app import analysis

    c = [float(i) for i in range(1, 11)]  # 1..10 mmol/L
    # 含背景电导：κblank=15、Λ0=95、K=8
    y = [15 + 95 * ci - 8 * ci**1.5 for ci in c]
    res = analysis.fit_kohlrausch(c, y)
    assert res is not None
    assert res["r2"] > 0.9999
    assert abs(res["params"]["a"] - 15.0) < 1e-6  # κblank
    assert abs(res["params"]["b"] - 95.0) < 1e-6  # Λ0
    assert abs(res["params"]["K"] - 8.0) < 1e-6   # K
    assert "Kohlrausch" in res["label"]


def test_kohlrausch_with_intercept_degrades_to_no_background():
    """无背景电导（κblank=0）时新模型应退化还原旧场景（截距≈0）。"""
    from app import analysis

    c = [float(i) for i in range(1, 11)]
    y = [ci * (100 - 10 * math.sqrt(ci)) for ci in c]  # 旧公式 y=c·(Λ∞−K·√c)
    res = analysis.fit_kohlrausch(c, y)
    assert res is not None
    assert res["r2"] > 0.9999
    assert abs(res["params"]["a"]) < 1e-6          # 截距≈0
    assert abs(res["params"]["b"] - 100.0) < 1e-6  # Λ0
    assert abs(res["params"]["K"] - 10.0) < 1e-6   # K


def test_arrhenius_activation_energy():
    """Arrhenius：κ = 5·e^(−3000/(R·T))，输出 Ea ≈ 3 kJ/mol、a ≈ 5。"""
    from app import analysis

    x = [float(i) for i in range(15, 40)]  # 15..39 °C
    y = [5 * math.exp(-3000.0 / (8.314 * (xi + 273.15))) for xi in x]
    res = analysis.fit_arrhenius(x, y)
    assert res is not None
    assert res["r2"] > 0.9999
    assert abs(res["params"]["a"] - 5.0) < 0.05
    assert abs(res["params"]["Ea_kJ_mol"] - 3.0) < 0.05


def test_arrhenius_rejects_constant_or_too_narrow_temperature_span():
    from app import analysis

    assert analysis.fit_arrhenius([25.0, 25.0, 25.0], [1.0, 1.1, 1.2]) is None
    assert analysis.fit_arrhenius([25.0, 25.2, 25.4], [1.0, 1.1, 1.2]) is None


def test_x_axis_filters_model_pool():
    """temperature 轴模型池应含 Arrhenius 但不含 Kohlrausch；跨轴模型被跳过。"""
    from app import analysis

    x = [float(i) for i in range(15, 40)]
    y = [5 * math.exp(-3000.0 / (8.314 * (xi + 273.15))) for xi in x]
    results = analysis.fit_all(x, y, x_axis="temperature")
    models = {r["model"] for r in results}
    assert "arrhenius" in models
    assert "kohlrausch" not in models
    assert "first_order" not in models

    # 显式请求跨轴模型（concentration 的 kohlrausch）应被跳过
    results2 = analysis.fit_all(x, y, models=["arrhenius", "kohlrausch"], x_axis="temperature")
    assert [r["model"] for r in results2] == ["arrhenius"]


def test_explicit_empty_model_list_runs_nothing():
    from app import analysis

    assert analysis.fit_all([1.0, 2.0, 3.0], [2.0, 4.0, 6.0], models=[]) == []


def test_fit_report_metrics_and_interval():
    """REQ-F：拟合输出 MAE/AICc/区间/禁止外推；线性模型含参数 CI。"""
    from app import analysis

    x = [float(i) for i in range(8)]
    y = [2 + 3 * xi + (0.2 if i % 2 else -0.2) for i, xi in enumerate(x)]
    res = analysis.fit_linear(x, y)
    assert res is not None
    assert res["mae"] > 0
    assert res["aicc"] is not None
    assert res["x_min"] == 0.0
    assert res["x_max"] == 7.0
    assert res["extrapolation_forbidden"] is True
    assert res["param_ci"] is not None
    assert res["param_ci"]["a"][0] < 2.0 < res["param_ci"]["a"][1]
    assert res["param_ci"]["b"][0] < 3.0 < res["param_ci"]["b"][1]


def test_fit_all_attaches_loocv():
    from app import analysis

    x = [float(i) for i in range(10)]
    y = [1 + 2 * xi for xi in x]
    results = analysis.fit_all(x, y, models=["linear"])
    assert results[0]["loocv_rmse"] is not None
    assert results[0]["loocv_rmse"] < 1e-6


def test_fit_endpoint_ok(client):
    r = client.post(
        "/api/analysis/fit",
        json={"x": [0, 1, 2, 3, 4], "y": [2, 5, 8, 11, 14], "models": ["linear", "quadratic"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["best"] == "linear"
    assert len(body["models"]) == 2
    linear = next(m for m in body["models"] if m["model"] == "linear")
    assert abs(linear["params"]["b"] - 3.0) < 1e-6
    assert linear["r2"] > 0.9999
    assert len(linear["fitted"]) == 150
    assert linear["mae"] is not None
    assert linear["extrapolation_forbidden"] is True
    assert linear["x_min"] == 0
    assert linear["x_max"] == 4


def test_fit_endpoint_x_axis_kohlrausch(client):
    """concentration 轴下 /api/analysis/fit 应可用 Kohlrausch（含截距）模型。"""
    c = [float(i) for i in range(1, 9)]
    y = [15 + 120 * ci - 8 * ci**1.5 for ci in c]  # κblank=15, Λ0=120, K=8
    r = client.post(
        "/api/analysis/fit",
        json={"x": c, "y": y, "models": ["kohlrausch", "linear"], "x_axis": "concentration"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["best"] == "kohlrausch"
    kohl = next(m for m in body["models"] if m["model"] == "kohlrausch")
    assert abs(kohl["params"]["a"] - 15.0) < 1e-6   # κblank
    assert abs(kohl["params"]["b"] - 120.0) < 1e-6  # Λ0
    assert abs(kohl["params"]["K"] - 8.0) < 1e-6    # K


def test_fit_endpoint_persists_when_experiment_id_given(client, tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DERIVED_DIR", str(tmp_path / "derived"))
    exp_id = client.post("/api/experiment/start").json()["experiment_id"]
    client.post("/api/experiment/stop")
    r = client.post(
        "/api/analysis/fit",
        json={
            "x": [0, 1, 2, 3, 4],
            "y": [2, 5, 8, 11, 14],
            "models": ["linear"],
            "experiment_id": exp_id,
        },
    )
    assert r.status_code == 200
    assert r.json()["derived_path"]
    detail = client.get(f"/api/experiments/{exp_id}").json()
    assert detail["fits"]
    assert detail["fits"][0]["model"] == "linear"
    assert detail["fits"][0]["x_axis"] == "time"
    client.post("/api/experiment/reset")


def test_fit_endpoint_bad_input(client):
    # 点数不足：schema min_length=3 → 422
    assert client.post("/api/analysis/fit", json={"x": [1, 2], "y": [1, 2]}).status_code == 422
    # 长度不等（两边都过 min_length）
    assert client.post("/api/analysis/fit", json={"x": [1, 2, 3], "y": [1, 2, 3, 4]}).status_code == 400
    # 过短的 y 同样被 schema 拒绝
    assert client.post("/api/analysis/fit", json={"x": [1, 2, 3], "y": [1, 2]}).status_code == 422
    # 非法 x_axis
    assert (
        client.post(
            "/api/analysis/fit",
            json={"x": [1, 2, 3], "y": [1, 2, 3], "x_axis": "bogus"},
        ).status_code
        == 422
    )


def test_fit_all_rejects_nonfinite_results():
    """溢出数据不得产出 NaN/inf 结果：全部结果必须能通过严格 JSON 序列化。"""
    import json as _json

    from app.analysis import fit_all

    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [1e308, -1e308, 1e308, -1e308, 1e308]
    results = fit_all(x, y, x_axis="time")
    # 旧实现在此抛 ValueError（NaN/inf 无法以 allow_nan=False 序列化）
    _json.dumps(results, allow_nan=False)
    for r in results:
        for v in r["params"].values():
            assert math.isfinite(v)
        assert math.isfinite(r["r2"])
        assert r["loocv_rmse"] is None or math.isfinite(r["loocv_rmse"])


def test_result_is_finite_gate():
    """有限性闸门单元测试：任一核心数值非有限即拒绝整个拟合结果。"""
    from app.analysis import _result_is_finite

    ok = {
        "params": {"a": 1.0, "b": -2.0},
        "r2": 0.99,
        "rmse": 0.1,
        "mae": 0.05,
        "residual_max_abs": 0.2,
        "loocv_rmse": 0.12,
        "fitted": [[0.0, 1.0], [1.0, -1.0]],
        "param_ci": {"a": [0.5, 1.5]},
    }
    assert _result_is_finite(ok)
    assert _result_is_finite({**ok, "mae": None, "param_ci": None})  # 允许缺失项

    for mutate in (
        {"params": {"a": float("nan")}},
        {"params": {"a": float("-inf")}},
        {"r2": float("inf")},
        {"rmse": float("nan")},
        {"mae": float("inf")},
        {"residual_max_abs": float("nan")},
        {"loocv_rmse": float("inf")},
        {"fitted": [[0.0, float("nan")]]},
        {"param_ci": {"a": [float("inf"), 1.5]}},
    ):
        assert not _result_is_finite({**ok, **mutate}), mutate


# ---------------- 退化数据、数值稳定性与 Kohlrausch ----------------


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


def test_fit_all_constant_y_returns_no_results():
    y = [1413.0] * 20
    x = list(range(20))
    assert analysis.fit_all(x, y, ["linear", "quadratic"], "time") == []


def test_fit_linear_still_works_for_normal_data():
    res = analysis.fit_linear([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0], "time")
    assert res is not None
    assert abs(res["params"]["b"] - 2.0) < 1e-9
    assert res["r2"] > 0.9999


def test_polyfit_large_x_linear():
    # concentration 轴可达 1e4：修复前正规方程条件数爆炸/误判奇异
    x = [10_000.0 + 3.0 * i for i in range(30)]
    y = [1.5 * xi + 200.0 for xi in x]
    a, b = analysis._polyfit(x, y, 1)
    assert abs(b - 1.5) < 1e-6
    assert abs(a - 200.0) < 1e-3


def test_polyfit_large_x_quadratic():
    x = [800.0 + 0.7 * i for i in range(40)]
    y = [0.001 * xi**2 - 0.5 * xi + 10.0 for xi in x]
    a, b, c = analysis._polyfit(x, y, 2)
    assert abs(c - 0.001) < 1e-9
    assert abs(b + 0.5) < 1e-6
    assert abs(a - 10.0) < 1e-3


def test_fit_all_temperature_axis_large_values():
    # 温度轴 ~1e2 量级 + y 有真实斜率：linear 温补模型必须能解出
    x = [25.0 + 0.1 * i for i in range(50)]
    y = [1400.0 * (1.0 + 0.02 * (xi - 25.0)) for xi in x]
    results = analysis.fit_all(x, y, ["linear"], "temperature")
    assert results, "linear 温补模型在大 x 量级下不应被静默跳过"
    assert abs(results[0]["params"]["b"] - 28.0) < 1e-6


def test_kohlrausch_accepts_blank_sample_and_rejects_negative():
    c = [0.0, 1.0, 2.0, 4.0, 8.0]
    res = analysis.fit_kohlrausch(c, [15 + 95 * ci - 8 * ci**1.5 for ci in c])
    assert res is not None
    assert abs(res["params"]["a"] - 15.0) < 1e-6  # κblank 由 c=0 空白样锚定
    assert analysis.fit_kohlrausch([-1.0, 1.0, 2.0], [1.0, 2.0, 3.0]) is None
