"""判稳与 QC 算法单测（REQ-D-003）。

覆盖新路线图 N5 验收标准①：
- 构造稳定序列 → PASS（代表值=窗口均值）
- 注入线性漂移 → FAIL(reason=drift)
- 注入阶跃跳变 → 窗口内变异大 → FAIL(reason=high_variation)
- 硬质量标志（饱和/开路/短路）→ FAIL(reason=hard_quality_flag)
- 样本不足 → WARN(reason=insufficient_samples)
"""

import pytest

from app.stability import StabilityConfig, check_stability


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
    flags = [""] * 35 + ["SATURATED"]
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


def test_timestamps_affect_slope():
    # 用真实时间戳：同样数值增量，时间跨度 5 倍 → 斜率归一化后 1/5
    values = [100.0 + 1.0 * i for i in range(20)]
    ts_1s = list(range(20))
    ts_5s = [i * 5 for i in range(20)]
    r1 = check_stability(values, timestamps=ts_1s)
    r2 = check_stability(values, timestamps=ts_5s)
    assert r1.slope is not None and r2.slope is not None
    assert r2.slope == pytest.approx(r1.slope / 5.0, rel=1e-9)
