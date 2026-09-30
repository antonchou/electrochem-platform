"""判稳（QC）：看测量尾部一个时间窗口内的读数是否稳定，给出 PASS / WARN / FAIL、原因与代表值。

判据（阈值是台架标定前的初设值，随每次测量存档，便于日后复核）：
- 窗口内有硬异常标志 → FAIL；有提示标志 → WARN
- 无效帧（κ25 算不出或带硬异常）占比 ≥ invalid_fail → FAIL；有但不到 → WARN
- 有效点数 < min_points → FAIL
- 变异系数 CV = 标准差 / 均值；相对漂移 = 窗口内线性趋势造成的总变化 / 均值。各有 WARN / FAIL 两档
- 窗口内温度跨度 > temp_span_warn_c → WARN（温补误差随温差变大）
- 测量时长不到一个窗口 → WARN（稳定性没在完整窗口上得到证明）

相对量在低电导率（如纯水）时会被放大：均值低于 kappa_floor_us_cm 时按这个下限算相对量。
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .frames import HARD_FLAGS, SOFT_FLAGS

FAIL_CODES = frozenset({"no_data", "hard_flags", "too_many_invalid", "insufficient_points", "high_variation", "drift"})


@dataclass(frozen=True, slots=True)
class QcConfig:
    window_s: float = 30.0
    min_points: int = 10
    cv_warn: float = 0.005
    cv_fail: float = 0.02
    drift_warn: float = 0.005
    drift_fail: float = 0.02
    temp_span_warn_c: float = 0.5
    invalid_fail: float = 0.2
    kappa_floor_us_cm: float = 10.0


@dataclass(frozen=True, slots=True)
class QcPoint:
    t_s: float
    kappa25_us_cm: float | None
    temperature_c: float | None
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QcResult:
    verdict: str  # PASS / WARN / FAIL
    reasons: tuple[str, ...]
    hard_flags: tuple[str, ...]
    n_points: int
    n_valid: int
    window_s: float
    kappa25_mean: float | None
    kappa25_sd: float | None
    cv: float | None
    drift: float | None
    temperature_mean: float | None
    temperature_span: float | None

    @property
    def representative_kappa25(self) -> float | None:
        """代表值：窗口内有效 κ25 的均值；FAIL 时没有代表值。"""
        return None if self.verdict == "FAIL" else self.kappa25_mean

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["reasons"] = list(self.reasons)
        out["hard_flags"] = list(self.hard_flags)
        out["representative_kappa25"] = self.representative_kappa25
        return out


def _slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def assess(points: Sequence[QcPoint], config: QcConfig = QcConfig()) -> QcResult:
    """points 按时间升序。只看最后 window_s 秒。"""
    if not points:
        return QcResult("FAIL", ("no_data",), (), 0, 0, 0.0, None, None, None, None, None, None)
    t_end = points[-1].t_s
    window = [p for p in points if p.t_s >= t_end - config.window_s]
    span = t_end - window[0].t_s
    reasons: list[str] = []

    hard = sorted({f for p in window for f in p.flags if f in HARD_FLAGS})
    if hard:
        reasons.append("hard_flags")
    if any(f in SOFT_FLAGS for p in window for f in p.flags):
        reasons.append("waveform_unstable")

    valid = [p for p in window if p.kappa25_us_cm is not None and not HARD_FLAGS.intersection(p.flags)]
    invalid_ratio = 1 - len(valid) / len(window)
    if invalid_ratio >= config.invalid_fail:
        reasons.append("too_many_invalid")
    elif invalid_ratio > 0:
        reasons.append("some_invalid")
    if span < 0.9 * config.window_s:
        reasons.append("window_incomplete")

    mean = sd = cv = drift = t_mean = t_span = None
    if len(valid) < config.min_points:
        reasons.append("insufficient_points")
    if valid:
        values = [p.kappa25_us_cm for p in valid]
        temps = [p.temperature_c for p in valid if p.temperature_c is not None]
        mean = statistics.fmean(values)
        sd = statistics.stdev(values) if len(values) >= 2 else 0.0
        scale = max(abs(mean), config.kappa_floor_us_cm)
        cv = sd / scale
        times = [p.t_s for p in valid]
        drift = _slope(times, values) * (times[-1] - times[0]) / scale if len(valid) >= 2 else 0.0
        if temps:
            t_mean = statistics.fmean(temps)
            t_span = max(temps) - min(temps)
        if len(valid) >= config.min_points:
            if cv > config.cv_fail:
                reasons.append("high_variation")
            elif cv > config.cv_warn:
                reasons.append("variation")
            if abs(drift) > config.drift_fail:
                reasons.append("drift")
            elif abs(drift) > config.drift_warn:
                reasons.append("slight_drift")
        if t_span is not None and t_span > config.temp_span_warn_c:
            reasons.append("temperature_unstable")

    if FAIL_CODES.intersection(reasons):
        verdict = "FAIL"
    elif reasons:
        verdict = "WARN"
    else:
        verdict = "PASS"
    return QcResult(
        verdict, tuple(reasons), tuple(hard), len(window), len(valid), span, mean, sd, cv, drift, t_mean, t_span
    )


def config_dict(config: QcConfig) -> dict[str, Any]:
    return asdict(config)
