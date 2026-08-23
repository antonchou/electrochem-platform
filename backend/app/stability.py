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

import statistics
from dataclasses import dataclass, field
from typing import Literal

QcStatus = Literal["NONE", "PASS", "WARN", "FAIL"]


@dataclass(frozen=True, slots=True)
class StabilityConfig:
    """判稳阈值。默认值按工程经验初设，正式值须由台架数据标定后冻结。"""

    window: int = 30            # 滑动窗口点数（取序列尾部 N 点）
    min_samples: int = 10       # 低于此值视为样本不足（WARN，不判 FAIL，给机会）
    cv_warn: float = 0.01       # 变异系数软阈值（1%）
    cv_fail: float = 0.05       # 变异系数硬阈值（5%）
    slope_warn: float = 0.5     # 趋势斜率软阈值（μS/cm / 点）
    slope_fail: float = 2.0     # 趋势斜率硬阈值（μS/cm / 点）


@dataclass(frozen=True, slots=True)
class StabilityResult:
    status: QcStatus
    reason: str
    mean: float | None = None
    std: float | None = None
    cv: float | None = None
    slope: float | None = None
    n: int = 0
    representative_value: float | None = None


@dataclass(slots=True)
class _Window:
    """简单滑动窗口（数值序列）。"""

    values: list[float] = field(default_factory=list)
    maxlen: int = 30

    def push(self, value: float) -> None:
        self.values.append(value)
        if len(self.values) > self.maxlen:
            self.values.pop(0)

    @property
    def size(self) -> int:
        return len(self.values)


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

    window_values = values[-cfg.window :]
    n = len(window_values)
    if timestamps is not None:
        window_ts = timestamps[-cfg.window :]
    else:
        window_ts = list(range(n))

    # 硬异常：饱和/开路/短路/欠量程等质量标志出现在窗口内 → FAIL
    hard_flags = ("SATURATED", "OPEN_CIRCUIT", "SHORT_CIRCUIT", "UNDER_RANGE", "OUT_OF_RANGE")
    if quality_flags:
        window_flags = quality_flags[-cfg.window :]
        bad = sorted({f for flags in window_flags if flags for f in flags.split("|") if f in hard_flags})
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
        std=std,
        cv=cv,
        slope=slope,
        n=n,
        representative_value=representative,
    )


def _safe_mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None
