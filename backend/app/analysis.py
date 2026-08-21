"""备选公式拟合（M4 自动拟合平台前置）。

对已采集的数据用化学实验常见候选公式做最小二乘拟合，输出参数、R²、RMSE
与拟合曲线采样点，供前端对比"哪个公式最贴合"。

模型按 X 轴语义（x_axis）分组，化学意义优先：
- time          时间序列：线性 / 二次 / 一阶指数饱和 / 指数 / 对数 / 幂
                一阶指数饱和 y = a − b·e^(−k·x) 是传感器响应、扩散平衡等
                一阶过程的经典形式。
- temperature   EC-温度：线性温补 / 二次 / Arrhenius
                Arrhenius κ = a·e^(−Ea/(R·T))（T = x + 273.15，x 为 °C），
                输出活化能 Ea(kJ/mol)，用于温度校正分析。
- concentration EC-浓度：线性标定 / 二次 / Kohlrausch
                Kohlrausch（SRS 7.1）κ = κblank + Λ0·c − K·c^1.5，
                a=κblank（背景电导）、b=Λ0、K，保留截距。

实现为纯 Python（高斯消元解正规方程），不引入 numpy，保持树莓派轻量。
非线性模型（一阶指数饱和）通过"k 对数网格搜索 + 每点线性最小二乘"求解。
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional

# X 轴语义 → 模型清单（key 为模型标识，value 为展示用表达式）
MODELS: Dict[str, Dict[str, str]] = {
    "time": {
        "linear": "线性 y = a + b·x",
        "quadratic": "二次多项式 y = a + b·x + c·x²",
        "first_order": "一阶指数饱和 y = a − b·e^(−k·x)",
        "exponential": "指数 y = a·e^(b·x)",
        "logarithmic": "对数 y = a + b·ln(x)",
        "power": "幂函数 y = a·x^b",
    },
    "temperature": {
        "linear": "线性温补 y = a + b·T",
        "quadratic": "二次多项式 y = a + b·T + c·T²",
        "arrhenius": "Arrhenius y = a·e^(−Ea/(R·T))",
    },
    "concentration": {
        "linear": "线性标定 y = a + b·c",
        "quadratic": "二次多项式 y = a + b·c + c·c²",
        "kohlrausch": "Kohlrausch y = a + b·c − K·c^1.5",
    },
}

FIT_SAMPLES = 150  # 拟合曲线采样点数
GAS_CONST = 8.314  # J/(mol·K)，用于 Arrhenius 活化能换算


def _solve_linear_system(a: List[List[float]], b: List[float]) -> List[float]:
    """高斯消元求解 a·x = b（a 为 n×n 方阵）。"""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        pv = m[col][col]
        if abs(pv) < 1e-12:
            raise ValueError("normal equations matrix is singular")
        for r in range(n):
            if r == col:
                continue
            factor = m[r][col] / pv
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def _polyfit(x: List[float], y: List[float], deg: int) -> List[float]:
    """多项式最小二乘，返回升幂系数 [c0, c1, ..., c_deg]（解正规方程）。"""
    n = deg + 1
    a = [[sum(xi ** (i + j) for xi in x) for j in range(n)] for i in range(n)]
    b = [sum(xi**i * yi for xi, yi in zip(x, y)) for i in range(n)]
    return _solve_linear_system(a, b)


def _linfit_cols(cols: List[List[float]], y: List[float]) -> List[float]:
    """多列线性最小二乘：y = Σ c_j·col_j，cols 为每个样本的各列值。"""
    m = len(cols[0])
    a = [[0.0] * m for _ in range(m)]
    b = [0.0] * m
    for i in range(m):
        for j in range(m):
            a[i][j] = sum(r[i] * r[j] for r in cols)
        b[i] = sum(r[i] * yi for r, yi in zip(cols, y))
    return _solve_linear_system(a, b)


def _metrics(y: List[float], y_hat: List[float]) -> tuple[float, float]:
    n = len(y)
    mean = sum(y) / n
    ss_res = sum((yi - yh) ** 2 for yi, yh in zip(y, y_hat))
    ss_tot = sum((yi - mean) ** 2 for yi in y)
    r2 = 1.0 if ss_tot < 1e-12 else 1.0 - ss_res / ss_tot
    rmse = math.sqrt(ss_res / n)
    return r2, rmse


def _sample_curve(x: List[float], fn: Callable[[float], float]) -> List[List[float]]:
    x_min, x_max = min(x), max(x)
    if abs(x_max - x_min) < 1e-9:
        return [[x_min, fn(x_min)]]
    step = (x_max - x_min) / (FIT_SAMPLES - 1)
    return [[round(x_min + i * step, 6), round(fn(x_min + i * step), 6)] for i in range(FIT_SAMPLES)]


def _label(model: str, axis: str) -> str:
    return MODELS.get(axis, MODELS["time"]).get(model, model)


def fit_linear(x: List[float], y: List[float], axis: str = "time") -> Optional[Dict[str, Any]]:
    if len(x) < 2:
        return None
    coeffs = _polyfit(x, y, 1)  # [a, b]
    a, b = coeffs
    y_hat = [a + b * xi for xi in x]
    r2, rmse = _metrics(y, y_hat)
    return {
        "model": "linear",
        "label": _label("linear", axis),
        "params": {"a": a, "b": b},
        "r2": r2,
        "rmse": rmse,
        "n": len(x),
        "fitted": _sample_curve(x, lambda xi: a + b * xi),
    }


def fit_quadratic(x: List[float], y: List[float], axis: str = "time") -> Optional[Dict[str, Any]]:
    if len(x) < 3:
        return None
    a, b, c = _polyfit(x, y, 2)
    y_hat = [a + b * xi + c * xi * xi for xi in x]
    r2, rmse = _metrics(y, y_hat)
    return {
        "model": "quadratic",
        "label": _label("quadratic", axis),
        "params": {"a": a, "b": b, "c": c},
        "r2": r2,
        "rmse": rmse,
        "n": len(x),
        "fitted": _sample_curve(x, lambda xi: a + b * xi + c * xi * xi),
    }


def fit_exponential(x: List[float], y: List[float], axis: str = "time") -> Optional[Dict[str, Any]]:
    """y = a·e^(b·x) → ln(y) = ln(a) + b·x（要求 y>0）。"""
    if len(x) < 2 or any(yi <= 0 for yi in y):
        return None
    ln_y = [math.log(yi) for yi in y]
    ln_a, b = _polyfit(x, ln_y, 1)
    a = math.exp(ln_a)
    y_hat = [a * math.exp(b * xi) for xi in x]
    r2, rmse = _metrics(y, y_hat)
    return {
        "model": "exponential",
        "label": _label("exponential", axis),
        "params": {"a": a, "b": b},
        "r2": r2,
        "rmse": rmse,
        "n": len(x),
        "fitted": _sample_curve(x, lambda xi: a * math.exp(b * xi)),
    }


def fit_logarithmic(x: List[float], y: List[float], axis: str = "time") -> Optional[Dict[str, Any]]:
    """y = a + b·ln(x)（要求 x>0）。"""
    if len(x) < 2 or any(xi <= 0 for xi in x):
        return None
    ln_x = [math.log(xi) for xi in x]
    a, b = _polyfit(ln_x, y, 1)
    y_hat = [a + b * math.log(xi) for xi in x]
    r2, rmse = _metrics(y, y_hat)
    return {
        "model": "logarithmic",
        "label": _label("logarithmic", axis),
        "params": {"a": a, "b": b},
        "r2": r2,
        "rmse": rmse,
        "n": len(x),
        "fitted": _sample_curve(x, lambda xi: a + b * math.log(xi)),
    }


def fit_power(x: List[float], y: List[float], axis: str = "time") -> Optional[Dict[str, Any]]:
    """y = a·x^b → ln(y) = ln(a) + b·ln(x)（要求 x>0, y>0）。"""
    if len(x) < 2 or any(xi <= 0 for xi in x) or any(yi <= 0 for yi in y):
        return None
    ln_x = [math.log(xi) for xi in x]
    ln_y = [math.log(yi) for yi in y]
    ln_a, b = _polyfit(ln_x, ln_y, 1)
    a = math.exp(ln_a)
    y_hat = [a * xi**b for xi in x]
    r2, rmse = _metrics(y, y_hat)
    return {
        "model": "power",
        "label": _label("power", axis),
        "params": {"a": a, "b": b},
        "r2": r2,
        "rmse": rmse,
        "n": len(x),
        "fitted": _sample_curve(x, lambda xi: a * xi**b),
    }


def fit_first_order(x: List[float], y: List[float], axis: str = "time") -> Optional[Dict[str, Any]]:
    """y = a − b·e^(−k·x)：一阶指数饱和（传感器响应 / 扩散平衡趋稳）。

    非线性模型：对 k 做对数网格搜索，每个 k 下 [a, b] 关于列 [1, −e^(−k·x)]
    是线性最小二乘，取 RMSE 最小的一组。k 的搜索范围由 x 跨度自适应。
    """
    if len(x) < 3:
        return None
    dx = max(x) - min(x)
    if dx < 1e-9:
        return None
    best: Optional[Dict[str, Any]] = None
    steps = 60
    k_lo, k_hi = 0.02 / dx, 20.0 / dx
    for i in range(steps):
        k = k_lo * (k_hi / k_lo) ** (i / (steps - 1))
        dec = [-math.exp(-k * xi) for xi in x]
        try:
            a, b = _linfit_cols([[1.0, d] for d in dec], y)
        except ValueError:
            continue
        y_hat = [a + b * d for d in dec]
        r2, rmse = _metrics(y, y_hat)
        if best is None or rmse < best["_rmse"]:
            best = {"a": a, "b": b, "k": k, "r2": r2, "rmse": rmse, "_rmse": rmse}
    if best is None:
        return None
    a, b, k = best["a"], best["b"], best["k"]
    return {
        "model": "first_order",
        "label": _label("first_order", axis),
        "params": {"a": a, "b": b, "k": k},
        "r2": best["r2"],
        "rmse": best["rmse"],
        "n": len(x),
        "fitted": _sample_curve(x, lambda xi: a - b * math.exp(-k * xi)),
    }


def fit_arrhenius(x: List[float], y: List[float], axis: str = "temperature") -> Optional[Dict[str, Any]]:
    """Arrhenius：y = a·e^(−Ea/(R·T))，T = x + 273.15（x 为 °C）。

    线性化：ln(y) = ln(a) − (Ea/R)·(1/T)，对 1/T 做线性拟合，
    斜率 = −Ea/R，据此输出活化能 Ea(kJ/mol)。要求 y>0。
    """
    if len(x) < 3 or any(yi <= 0 for yi in y):
        return None
    t_k = [xi + 273.15 for xi in x]
    if any(ti <= 0 for ti in t_k):
        return None
    inv_t = [1.0 / ti for ti in t_k]
    ln_y = [math.log(yi) for yi in y]
    ln_a, slope = _polyfit(inv_t, ln_y, 1)  # slope = −Ea/R
    a = math.exp(ln_a)
    ea = -slope * GAS_CONST  # J/mol
    y_hat = [a * math.exp(-ea / (GAS_CONST * ti)) for ti in t_k]
    r2, rmse = _metrics(y, y_hat)
    return {
        "model": "arrhenius",
        "label": _label("arrhenius", axis),
        "params": {"a": a, "Ea_kJ_mol": ea / 1000.0},
        "r2": r2,
        "rmse": rmse,
        "n": len(x),
        "fitted": _sample_curve(x, lambda xi: a * math.exp(-ea / (GAS_CONST * (xi + 273.15)))),
    }


def fit_kohlrausch(x: List[float], y: List[float], axis: str = "concentration") -> Optional[Dict[str, Any]]:
    """Kohlrausch（含背景电导截距）：y = a + b·x − K·x^1.5（x 为浓度 c）。

    对应 SRS 7.1：κ = κblank + Λ0·c − K·c^1.5，
    a=κblank（背景电导）、b=Λ0（极限摩尔电导率）、K（经验常数）。
    保留截距是本模型的关键：强制过原点（旧实现 y=c·(a−b·√c)）会系统性
    高估 Λ0 与 K（评审 B-1）。对 [a, b, K] 是线性最小二乘
    （自变量列 [1, c, −c^1.5]）。要求 c>0。
    """
    if len(x) < 3 or any(xi <= 0 for xi in x):
        return None
    try:
        # 特征 [1, c, −c^1.5] 中 c 与 c^1.5 高度共线，正规方程病态。
        # 用 Gram-Schmidt 一步正交化：c^1.5 对 [1, c] 回归取残差列 r（与 1、c 正交），
        # 再对 [1, c, r] 拟合，最后把系数还原回 y = a + b·c − K·c^1.5。
        x15 = [xi**1.5 for xi in x]
        c0, c1 = _polyfit(x, x15, 1)  # c^1.5 ≈ c0 + c1·c
        resid = [v - (c0 + c1 * xi) for xi, v in zip(x, x15)]
        alpha, beta, gamma = _linfit_cols([[1.0, xi, r] for xi, r in zip(x, resid)], y)
    except ValueError:
        return None
    a = alpha - gamma * c0
    b = beta - gamma * c1
    k = -gamma
    y_hat = [a + b * xi - k * xi**1.5 for xi in x]
    r2, rmse = _metrics(y, y_hat)
    return {
        "model": "kohlrausch",
        "label": _label("kohlrausch", axis),
        "params": {"a": a, "b": b, "K": k},
        "r2": r2,
        "rmse": rmse,
        "n": len(x),
        "fitted": _sample_curve(x, lambda xi: a + b * xi - k * xi**1.5),
    }


FITTERS = {
    "linear": fit_linear,
    "quadratic": fit_quadratic,
    "exponential": fit_exponential,
    "logarithmic": fit_logarithmic,
    "power": fit_power,
    "first_order": fit_first_order,
    "arrhenius": fit_arrhenius,
    "kohlrausch": fit_kohlrausch,
}


def fit_all(
    x: List[float],
    y: List[float],
    models: Optional[List[str]] = None,
    x_axis: str = "time",
) -> List[Dict[str, Any]]:
    """对指定（或该轴全部）模型执行拟合，按 R² 从高到低排序。

    传入的模型若不属于当前 x_axis 的模型池则跳过（保证化学语境正确）。
    """
    available = MODELS.get(x_axis, MODELS["time"])
    selected = models if models else list(available.keys())
    results: List[Dict[str, Any]] = []
    for name in selected:
        if name not in available:
            continue
        fitter = FITTERS.get(name)
        if fitter is None:
            continue
        try:
            res = fitter(x, y, axis=x_axis)
        except (ValueError, OverflowError, ZeroDivisionError):
            res = None
        if res is not None:
            results.append(res)
    results.sort(key=lambda r: -r["r2"])
    return results
