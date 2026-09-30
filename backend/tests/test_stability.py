"""判稳与 QC 算法单测（REQ-D-003）。

覆盖新路线图 N5 验收标准①：
- 构造稳定序列 → PASS（代表值=窗口均值）
- 注入线性漂移 → FAIL(reason=drift)
- 注入阶跃跳变 → 窗口内变异大 → FAIL(reason=high_variation)
- 硬质量标志（饱和/开路/短路）→ FAIL(reason=hard_quality_flag)
- 样本不足 → WARN(reason=insufficient_samples)
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.main import app
from app.stability import StabilityConfig, check_stability, qc_from_frames


def _stable_series(n: int = 40, base: float = 100.0, noise: float = 0.3):
    import random

    rng = random.Random(7)
    return [base + rng.uniform(-noise, noise) for _ in range(n)]


def test_stable_series_passes():
    values = _stable_series()
    result = check_stability(values)
    assert result.status == "PASS"
    assert result.reason == "stable"
    assert result.representative_value is not None
    assert result.mean is not None
    assert 90 < result.mean < 110
    assert result.cv is not None and result.cv < 0.01
    assert result.median is not None
    assert 90 < result.median < 110


def test_drift_fails():
    # 线性漂移：每个点 +3 μS/cm，斜率 3.0 > slope_fail(2.0) → FAIL drift
    values = [100.0 + 3.0 * i for i in range(40)]
    result = check_stability(values)
    assert result.status == "FAIL"
    assert result.reason == "drift"


def test_jump_high_variation_fails():
    # 噪声突发：均值不变（100），但窗口内波动剧烈 → cv 超标、斜率≈0 → high_variation
    import random

    rng = random.Random(3)
    values = [100.0] * 15 + [100.0 + rng.uniform(-20, 20) for _ in range(10)] + [100.0] * 15
    result = check_stability(values)
    assert result.status == "FAIL"
    assert result.reason == "high_variation"


def test_hard_quality_flag_fails():
    values = _stable_series()
    flags = [""] * (len(values) - 1) + ["SATURATED"]
    result = check_stability(values, quality_flags=flags)
    assert result.status == "FAIL"
    assert result.reason == "hard_quality_flag"


def test_insufficient_samples_warns():
    result = check_stability([100.0, 100.1, 99.9])
    assert result.status == "WARN"
    assert result.reason == "insufficient_samples"


def test_empty_data_fails():
    result = check_stability([])
    assert result.status == "FAIL"
    assert result.reason == "empty_data"


def test_borderline_variation_warns():
    # 噪声放大到介于 cv_warn(0.01) 与 cv_fail(0.05) 之间
    values = _stable_series(noise=2.5)
    result = check_stability(values)
    # base=100, noise=2.5 → cv≈2.5% → WARN (borderline_variation)
    assert result.status == "WARN"
    assert result.reason == "borderline_variation"


def test_config_thresholds_are_respected():
    # 高噪声但放宽阈值 → PASS
    values = _stable_series(noise=5.0)
    loose = StabilityConfig(cv_warn=0.20, cv_fail=0.50)
    result = check_stability(values, config=loose)
    assert result.status == "PASS"


def test_quality_flags_must_match_values_length():
    with pytest.raises(ValueError, match="same length"):
        check_stability([1.0, 2.0, 3.0], quality_flags=["SATURATED"])


def test_timestamps_affect_slope():
    # 用真实时间戳：同样数值增量，时间跨度 5 倍 → 斜率归一化后 1/5
    values = [100.0 + 1.0 * i for i in range(20)]
    ts_1s = list(range(20))
    ts_5s = [i * 5 for i in range(20)]
    r1 = check_stability(values, timestamps=ts_1s)
    r2 = check_stability(values, timestamps=ts_5s)
    assert r1.slope is not None and r2.slope is not None
    assert r2.slope == pytest.approx(r1.slope / 5.0, rel=1e-9)


def test_rate_scaled_config_unchanged_at_baseline_rate():
    from app.stability import rate_scaled_config

    ts = [i * 0.1 for i in range(40)]  # 10 Hz 基线
    cfg = rate_scaled_config(ts)
    assert cfg.slope_warn == pytest.approx(0.5)
    assert cfg.slope_fail == pytest.approx(2.0)


def test_rate_scaled_config_scales_with_sample_rate():
    from app.stability import rate_scaled_config

    ts = [i * 0.02 for i in range(40)]  # 50 Hz
    cfg = rate_scaled_config(ts)
    assert cfg.slope_warn == pytest.approx(0.5 * 10 / 50)
    assert cfg.slope_fail == pytest.approx(2.0 * 10 / 50)


def test_rate_scaled_config_handles_hold_frames_and_missing_ts():
    from app.stability import rate_scaled_config

    # CSV 回放保持帧：Δt=0 的对不参与，中位数仍来自有效步长（10 Hz）
    ts = [0.0, 0.1, 0.1, 0.2, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    cfg = rate_scaled_config(ts)
    assert cfg.slope_warn == pytest.approx(0.5)
    assert rate_scaled_config(None).slope_warn == pytest.approx(0.5)
    assert rate_scaled_config([1.0]).slope_warn == pytest.approx(0.5)
    assert rate_scaled_config([5.0, 5.0, 5.0]).slope_warn == pytest.approx(0.5)


def test_drift_verdict_is_rate_invariant():
    """同一物理漂移（μS/cm/s）在 10Hz 与 50Hz 采样下判定必须一致。"""
    from app.stability import rate_scaled_config

    for hz in (10, 50):
        n = 60
        ts = [i / hz for i in range(n)]
        # 30 μS/cm/s > 基线物理阈值 20（=2.0/点 × 10Hz）→ 任何速率都应 FAIL drift
        values = [100.0 + 30.0 * (i / hz) for i in range(n)]
        result = check_stability(values, config=rate_scaled_config(ts))
        assert result.status == "FAIL", f"{hz}Hz"
        assert result.reason == "drift", f"{hz}Hz"

    for hz in (10, 50):
        n = 60
        ts = [i / hz for i in range(n)]
        # 3 μS/cm/s < 基线物理软阈值 5 → 不应报 borderline_drift
        values = [100.0 + 3.0 * (i / hz) for i in range(n)]
        result = check_stability(values, config=rate_scaled_config(ts))
        assert result.reason != "borderline_drift", f"{hz}Hz"


# ---------------- 停止时 QC（qc_from_frames）与硬标志 ----------------


def test_stability_current_zero_is_hard_flag():
    values = [1400.0 + 0.1 * i for i in range(30)]
    flags = [""] * 29 + ["SIMULATED|CURRENT_ZERO"]
    result = check_stability(values, quality_flags=flags, config=StabilityConfig())
    assert result.status == "FAIL"
    assert result.reason == "hard_quality_flag"


def test_check_stability_rejects_mismatched_timestamps():
    """P2-6：timestamps 与 values 不等长必须 ValueError，不得静默截断算斜率。"""
    values = [1400.0 + i for i in range(10)]
    with pytest.raises(ValueError, match="timestamps"):
        check_stability(
            values,
            timestamps=[float(i) for i in range(5)],
            config=StabilityConfig(),
        )


def _row(k25, flags="SIMULATED", t=0.0):
    return {"kappa_25_us_cm": k25, "quality_flags": flags, "t_seconds": t}


def _stable_rows(n, start_t=0.0):
    return [_row(1413.0 + (0.1 if i % 2 else -0.1), t=start_t + i * 0.1) for i in range(n)]


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
