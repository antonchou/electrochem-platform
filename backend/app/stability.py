"""自动判稳与 QC（REQ-D-003）。

在时间窗口上计算统计量（均值/标准差/变异系数/趋势斜率），对照可配置阈值
给出 PASS / WARN / FAIL 判定与失败原因，并输出稳定段代表值。

设计（对应新路线图 N5）：
- 滑动窗口：取序列尾部最近 N 点（window）判定稳定性
- 统计量：mean、std、cv(=std/mean)、线性斜率 slope（索引或时间）
- 判定：先查样本数与异常质量，再查变异系数与趋势
- 纯函数、零 IO，阈值可配置（判稳参数按台架数据标定后冻结，不做拍脑袋参数）

判定语义：
- FAIL：样本不足 / 存在饱和等硬异常 / 变异或趋势超过硬阈值
- WARN：变异或趋势在软阈值与硬阈值之间，或样本量勉强够
- PASS：稳定，代表值 = 窗口均值
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from typing import Literal

QcStatus = Literal["NONE", "PASS", "WARN", "FAIL"]


@dataclass(frozen=True, slots=True)
class StabilityConfig:
    """判稳阈值。默认值按工程经验初设，正式值须由台架数据标定后冻结。

    slope_warn/slope_fail 以“μS/cm / 点”为单位，隐含 QC_BASELINE_RATE_HZ 基线采样率；
    其他采样率的数据须经 rate_scaled_config() 换算后再判稳，否则同一物理漂移
    在不同采样率下会得到不同判定。
    """

    window: int = 30            # 滑动窗口点数（取序列尾部 N 点）
    min_samples: int = 10       # 低于此值视为样本不足（WARN，不判 FAIL，给机会）
    cv_warn: float = 0.01       # 变异系数软阈值（1%）
    cv_fail: float = 0.05       # 变异系数硬阈值（5%）
    slope_warn: float = 0.5     # 趋势斜率软阈值（μS/cm / 点，按 10 Hz 基线）
    slope_fail: float = 2.0     # 趋势斜率硬阈值（μS/cm / 点，按 10 Hz 基线）
    # 窗口覆盖的原始帧中“无效帧”（计算链拒绝 COMPUTE_INVALID / 缺 κ25）占比，仅 qc_from_frames 使用
    invalid_warn: float = 0.0   # 超过即 WARN（默认：出现任何无效帧）
    invalid_fail: float = 0.2   # 达到即 FAIL（20%）


# slope 阈值“每点”语义的基线采样率（当前 Mock/主链路为 10 Hz）。
QC_BASELINE_RATE_HZ = 10.0

# 硬异常：饱和/开路/短路/欠量程/越界/电流归零等质量标志出现在窗口内 → FAIL。
# CURRENT_ZERO（模拟器故障注入，T-16）语义上属硬失效，判稳不得给出 PASS/WARN。
HARD_FLAGS = frozenset(
    {
        "SATURATED",
        "OPEN_CIRCUIT",
        "SHORT_CIRCUIT",
        "UNDER_RANGE",
        "OUT_OF_RANGE",
        "CURRENT_ZERO",
    }
)


def rate_scaled_config(
    timestamps: list[float] | None,
    base: StabilityConfig | None = None,
) -> StabilityConfig:
    """按真实采样率缩放 slope 阈值，使 QC 判定与采样率无关。

    每“点”阈值隐含基线采样率；采样率 r 下的每点斜率是物理漂移（μS/cm/s）
    的 1/r，故阈值需乘 QC_BASELINE_RATE_HZ / r。时间戳不足、无正步长或
    步长非有限值时返回原配置。采样率取窗口尾部的 Δt 中位数，与 slope 所见
    的窗口一致（CSV 回放可能存在同值保持帧，Δt=0 的对不参与统计）。
    """
    cfg = base or StabilityConfig()
    if not timestamps or len(timestamps) < 2:
        return cfg
    window_ts = timestamps[-cfg.window :]
    diffs = sorted(b - a for a, b in zip(window_ts, window_ts[1:]) if b - a > 0)
    if not diffs:
        return cfg
    median_dt = diffs[len(diffs) // 2]
    rate = 1.0 / median_dt
    if not math.isfinite(rate) or rate <= 0:
        return cfg
    factor = QC_BASELINE_RATE_HZ / rate
    return replace(cfg, slope_warn=cfg.slope_warn * factor, slope_fail=cfg.slope_fail * factor)


@dataclass(frozen=True, slots=True)
class StabilityResult:
    status: QcStatus
    reason: str
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    cv: float | None = None
    slope: float | None = None
    n: int = 0
    representative_value: float | None = None


def _linear_slope(xs: list[float], ys: list[float]) -> float:
    """一元线性回归斜率（least squares），按 x 单位。x 无变化时返回 0。"""
    n = len(xs)
    if n < 2:
        return 0.0
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    sxx = sum((x - x_mean) ** 2 for x in xs)
    if sxx < 1e-12:
        return 0.0
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    return sxy / sxx


def check_stability(
    values: list[float],
    *,
    config: StabilityConfig | None = None,
    timestamps: list[float] | None = None,
    quality_flags: list[str] | None = None,
) -> StabilityResult:
    """对序列做判稳，返回 QC 判定与统计量。

    - values：κ25（或任意物理量）序列，按时间序
    - timestamps：可选，用于斜率按真实时间步长计算；缺省按索引（每点 1 单位）
    - quality_flags：可选，逐点质量标志；命中饱和/开路/短路/欠量程等硬异常 → FAIL
    """
    cfg = config or StabilityConfig()
    if not values:
        return StabilityResult(status="FAIL", reason="empty_data", n=0)

    if quality_flags is not None and len(quality_flags) != len(values):
        raise ValueError(
            "quality_flags must be the same length as values "
            f"(got {len(quality_flags)} flags for {len(values)} values)"
        )
    if timestamps is not None and len(timestamps) != len(values):
        # 与 quality_flags 对称的等长断言（P2-6）：错配时 _linear_slope 的 zip
        # 会按较短序列静默截断，产出错误斜率进而污染 QC 判定。
        raise ValueError(
            "timestamps must be the same length as values "
            f"(got {len(timestamps)} timestamps for {len(values)} values)"
        )

    window_values = values[-cfg.window :]
    n = len(window_values)
    if timestamps is not None:
        window_ts = timestamps[-cfg.window :]
    else:
        window_ts = list(range(n))

    if quality_flags:
        window_flags = quality_flags[-cfg.window :]
        bad = sorted({f for flags in window_flags if flags for f in flags.split("|") if f in HARD_FLAGS})
        if bad:
            return StabilityResult(
                status="FAIL",
                reason="hard_quality_flag",
                n=n,
                mean=_safe_mean(window_values),
            )

    if n < cfg.min_samples:
        return StabilityResult(
            status="WARN",
            reason="insufficient_samples",
            n=n,
            mean=_safe_mean(window_values),
        )

    mean = statistics.fmean(window_values)
    median = statistics.median(window_values)
    std = statistics.stdev(window_values) if n >= 2 else 0.0
    cv = std / abs(mean) if abs(mean) > 1e-12 else float("inf")
    slope = _linear_slope(window_ts, window_values)

    representative = None
    status: QcStatus
    reason: str

    # 漂移优先：系统性趋势是比纯噪声变异更明确的失效信号
    # （纯斜坡的窗口 cv 天然偏高，应报 drift 而非 high_variation）
    if abs(slope) > cfg.slope_fail:
        status, reason = "FAIL", "drift"
    elif cv > cfg.cv_fail:
        status, reason = "FAIL", "high_variation"
    elif abs(slope) > cfg.slope_warn:
        status, reason = "WARN", "borderline_drift"
    elif cv > cfg.cv_warn:
        status, reason = "WARN", "borderline_variation"
    else:
        status, reason = "PASS", "stable"
        representative = mean

    return StabilityResult(
        status=status,
        reason=reason,
        mean=mean,
        median=median,
        std=std,
        cv=cv,
        slope=slope,
        n=n,
        representative_value=representative,
    )


def qc_series_from_frames(rows: list[dict]) -> tuple[list[float], list[str], list[float]]:
    """Build aligned κ25 / quality-flag / t_seconds series (numeric window only).

    Drops rows without κ25 and rows marked COMPUTE_INVALID so flags stay
    aligned with the numeric window. Hard flags on the dropped rows are lost
    here — stop-time QC therefore goes through qc_from_frames (09-30 #1).
    The returned timestamps cover exactly the kept rows so callers can
    estimate the sample rate (rate_scaled_config).
    """
    values: list[float] = []
    flags: list[str] = []
    timestamps: list[float] = []
    for row in rows:
        kappa25 = row.get("kappa_25_us_cm")
        if kappa25 is None:
            continue
        flag = row.get("quality_flags") or ""
        parts = [part for part in flag.split("|") if part]
        if "COMPUTE_INVALID" in parts:
            continue
        values.append(float(kappa25))
        flags.append(flag)
        t = row.get("t_seconds")
        timestamps.append(float(t) if t is not None else float("nan"))
    return values, flags, timestamps


def _flag_tokens(raw: str | None) -> list[str]:
    return [part for part in (raw or "").split("|") if part]


def _row_invalid(row: dict) -> bool:
    """κ25 缺失或被计算链拒绝（COMPUTE_INVALID）的帧不能进入数值窗口。"""
    return row.get("kappa_25_us_cm") is None or "COMPUTE_INVALID" in _flag_tokens(
        row.get("quality_flags")
    )


def _window_stats(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    std = statistics.stdev(values) if len(values) >= 2 else 0.0
    return statistics.fmean(values), statistics.median(values), std


def qc_from_frames(
    rows: list[dict],
    *,
    base: StabilityConfig | None = None,
) -> StabilityResult | None:
    """停止时 QC：对落库原始帧（旧→新）判稳（REQ-D-003）。

    数值窗口只取可计算的 κ25 帧；但“硬异常”与“无效帧占比”看的是数值窗口覆盖的
    全部原始帧——从窗口首个有效帧到最新一帧，含其间与末尾的无效帧。计算链拒绝
    （COMPUTE_INVALID）恰是故障最常见的形态（电压越界 U≤0、电极脱开），若随数值
    一起过滤，其上的 OUT_OF_RANGE 等硬标志会被丢掉，故障实验反判 PASS（09-30 审查 #1）。

    返回 None：原始帧不足 3 条，或从未算出过 κ25（旧 V1 帧），不写 QC。
    """
    if len(rows) < 3:
        return None
    kept_idx: list[int] = []
    values: list[float] = []
    flags: list[str] = []
    timestamps: list[float] = []
    for idx, row in enumerate(rows):
        if _row_invalid(row):
            continue
        kept_idx.append(idx)
        values.append(float(row["kappa_25_us_cm"]))
        flags.append(row.get("quality_flags") or "")
        t = row.get("t_seconds")
        timestamps.append(float(t) if t is not None else float("nan"))
    if not values and not any(
        "COMPUTE_INVALID" in _flag_tokens(row.get("quality_flags")) for row in rows
    ):
        return None

    # slope 阈值按真实采样率换算，判定与数据源速率（Mock 10Hz / CSV 50Hz…）无关
    cfg = rate_scaled_config(timestamps, base) if values else (base or StabilityConfig())
    window_values = values[-cfg.window :]
    span = rows[kept_idx[-cfg.window :][0] :] if kept_idx else rows
    mean, median, std = _window_stats(window_values)
    n = len(window_values)

    if any(tok in HARD_FLAGS for row in span for tok in _flag_tokens(row.get("quality_flags"))):
        return StabilityResult(
            status="FAIL", reason="hard_quality_flag", n=n, mean=mean, median=median, std=std
        )
    invalid_ratio = sum(1 for row in span if _row_invalid(row)) / len(span)
    if len(values) < 3 or invalid_ratio >= cfg.invalid_fail:
        return StabilityResult(
            status="FAIL", reason="invalid_frames", n=n, mean=mean, median=median, std=std
        )

    result = check_stability(values, quality_flags=flags, config=cfg)
    if invalid_ratio > cfg.invalid_warn and result.status == "PASS":
        # 数值本身稳定，但窗口里夹着算不出来的帧：代表值不可信，降为 WARN
        return replace(result, status="WARN", reason="some_invalid_frames", representative_value=None)
    return result


def _safe_mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None
