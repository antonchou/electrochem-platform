"""线性最小二乘，以及本实验用到的三个分析。三个分析都能化成直线：

- 浓度线性标定：κ25 = a + b·c
- Kohlrausch 定律（强电解质稀溶液）：Λm = Λ0 − K·√c，其中 Λm = κ25 / c
- 温度系数：κ(T) = κ25·(1 + α·(T − 25)) = a + b·(T − 25)，于是 κ25 = a、α = b / a
- 电池常数（标定）：κ标准 = Kcell · G25，过原点

参数给标准误与 95% 置信区间（t 分布）。结果只在数据覆盖的区间内有效，不做外推。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from .chemistry import finite

_T95 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
)

MIN_TEMPERATURE_SPAN_C = 2.0
MIN_TEMPERATURE_POINTS = 10


class FitError(ValueError):
    """数据不足或退化，拟合无意义。"""


def t95(dof: int) -> float:
    """双侧 95% 的 t 分位数。30 以内查表，更大时用 Cornish–Fisher 展开（误差 < 1e-3）。"""
    if dof < 1:
        raise ValueError("dof must be >= 1")
    if dof <= len(_T95):
        return _T95[dof - 1]
    z = 1.959964
    return z + (z**3 + z) / (4 * dof) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * dof**2)


@dataclass(frozen=True, slots=True)
class LineFit:
    slope: float
    intercept: float
    slope_se: float | None
    intercept_se: float | None
    cov: float | None  # cov(intercept, slope)
    r2: float | None
    n: int
    dof: int
    residuals: tuple[float, ...]

    def param(self, value: float, se: float | None) -> dict[str, Any]:
        ci = None
        if se is not None and self.dof >= 1:
            half = t95(self.dof) * se
            ci = [value - half, value + half]
        return {"value": value, "se": se, "ci95": ci}


def fit_line(xs: Sequence[float], ys: Sequence[float], *, through_origin: bool = False) -> LineFit:
    if len(xs) != len(ys):
        raise ValueError("xs and ys must have the same length")
    if not all(finite(v) for v in (*xs, *ys)):
        raise FitError("数据含非有限值")
    n = len(xs)
    my = sum(ys) / n if n else 0.0
    ss_tot = sum((y - my) ** 2 for y in ys)
    if through_origin:
        if n < 1:
            raise FitError("至少需要 1 个点")
        sxx = sum(x * x for x in xs)
        if sxx <= 0:
            raise FitError("x 全为 0")
        slope = sum(x * y for x, y in zip(xs, ys)) / sxx
        intercept = 0.0
        dof = n - 1
    else:
        if n < 2:
            raise FitError("至少需要 2 个点")
        mx = sum(xs) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx <= 0:
            raise FitError("x 没有变化，无法拟合直线")
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        intercept = my - slope * mx
        dof = n - 2
    residuals = tuple(y - (intercept + slope * x) for x, y in zip(xs, ys))
    ss_res = sum(r * r for r in residuals)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 and n >= 2 else None
    slope_se = intercept_se = cov = None
    if dof >= 1:
        s2 = ss_res / dof
        slope_se = math.sqrt(s2 / sxx)
        if not through_origin:
            intercept_se = math.sqrt(s2 * (1 / n + mx * mx / sxx))
            cov = -mx * s2 / sxx
    return LineFit(slope, intercept, slope_se, intercept_se, cov, r2, n, dof, residuals)


def _failure(exc: FitError) -> dict[str, Any]:
    return {"ok": False, "error": str(exc)}


def concentration_fit(points: Sequence[tuple[float, float]]) -> dict[str, Any]:
    """points = [(浓度 mmol/L, 代表 κ25 µS/cm)]。线性标定用全部点，Kohlrausch 只用 c > 0 的点。"""
    concentrations = [c for c, _ in points]
    out: dict[str, Any] = {
        "n": len(points),
        "range_mmol_l": [min(concentrations), max(concentrations)] if points else None,
    }
    try:
        fit = fit_line(concentrations, [k for _, k in points])
        out["linear"] = {
            "ok": True,
            "intercept_us_cm": fit.param(fit.intercept, fit.intercept_se),
            "slope_us_cm_per_mmol_l": fit.param(fit.slope, fit.slope_se),
            "r2": fit.r2,
            "n": fit.n,
            "residuals_us_cm": list(fit.residuals),
        }
    except FitError as exc:
        out["linear"] = _failure(exc)

    positive = [(c, k) for c, k in points if c > 0]
    try:
        if len(positive) < 3:
            raise FitError("Kohlrausch 拟合至少需要 3 个浓度 > 0 的点")
        xs = [math.sqrt(c / 1000.0) for c, _ in positive]  # √(mol/L)
        ys = [k / c for c, k in positive]  # Λm，S·cm²/mol
        fit = fit_line(xs, ys)
        out["kohlrausch"] = {
            "ok": True,
            "lambda0_s_cm2_per_mol": fit.param(fit.intercept, fit.intercept_se),
            "k_s_cm2_per_mol_sqrt_l_per_mol": fit.param(-fit.slope, fit.slope_se),
            "r2": fit.r2,
            "n": fit.n,
            "points": [{"sqrt_c": x, "molar_conductivity": y} for x, y in zip(xs, ys)],
            "residuals_s_cm2_per_mol": list(fit.residuals),
        }
    except FitError as exc:
        out["kohlrausch"] = _failure(exc)
    return out


def temperature_fit(points: Sequence[tuple[float, float]]) -> dict[str, Any]:
    """points = [(T °C, κ(T) µS/cm)]，来自一次变温测量。反推 κ25 与线性温补系数 α。"""
    try:
        if len(points) < MIN_TEMPERATURE_POINTS:
            raise FitError(f"至少需要 {MIN_TEMPERATURE_POINTS} 个有效点")
        temps = [t for t, _ in points]
        span = max(temps) - min(temps)
        if span < MIN_TEMPERATURE_SPAN_C:
            raise FitError(f"温度跨度 {span:.2f} °C 不足 {MIN_TEMPERATURE_SPAN_C:g} °C，无法估计 α")
        fit = fit_line([t - 25.0 for t in temps], [k for _, k in points])
        if fit.intercept <= 0:
            raise FitError("拟合的 κ25 不为正")
        alpha = fit.slope / fit.intercept
        alpha_se = None
        if fit.slope_se is not None and fit.intercept_se is not None and fit.cov is not None:
            # α = b / a 的一阶误差传播（含 a、b 协方差）
            var = (fit.slope_se**2 - 2 * alpha * fit.cov + alpha**2 * fit.intercept_se**2) / fit.intercept**2
            alpha_se = math.sqrt(max(var, 0.0))
        return {
            "ok": True,
            "kappa25_us_cm": fit.param(fit.intercept, fit.intercept_se),
            "alpha_per_c": fit.param(alpha, alpha_se),
            "r2": fit.r2,
            "n": fit.n,
            "temperature_range_c": [min(temps), max(temps)],
        }
    except FitError as exc:
        return _failure(exc)


def cell_constant_fit(points: Sequence[tuple[float, float]]) -> dict[str, Any]:
    """points = [(测得 G25 S, 标准液 κ25 µS/cm)]。过原点最小二乘求 Kcell（cm⁻¹）。

    抛 FitError：没有点或 G 全为 0。
    """
    fit = fit_line([g for g, _ in points], [k * 1e-6 for _, k in points], through_origin=True)
    if fit.slope <= 0:
        raise FitError("求得的 Kcell 不为正")
    deviations = [
        (fit.slope * g * 1e6 - k) / k * 100 if k else None for g, k in points
    ]
    return {
        "cell_constant_per_cm": fit.slope,
        "cell_constant": fit.param(fit.slope, fit.slope_se),
        "r2": fit.r2,
        "n": fit.n,
        "deviations_pct": deviations,
    }
