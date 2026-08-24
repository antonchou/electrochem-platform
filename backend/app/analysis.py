"""备选公式拟合（M4 自动拟合平台前置）。

对已采集的数据用化学实验常见候选公式做最小二乘拟合，输出参数、R²、RMSE、
MAE、AICc、残差峰值、有效区间（禁止外推）与可选 Wald CI / LOOCV RMSE。

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
        "quadratic": "二次多项式 y = a + b·c + q·c²",
        "kohlrausch": "Kohlrausch y = a + b·c − K·c^1.5",
    },
}

FIT_SAMPLES = 150  # 拟合曲线采样点数
GAS_CONST = 8.314  # J/(mol·K)，用于 Arrhenius 活化能换算
ARRHENIUS_MIN_TEMPERATURE_SPAN_C = 1.0


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


def _finite_or_none(value: float) -> Optional[float]:
    return value if math.isfinite(value) else None


def _invert(matrix: List[List[float]]) -> List[List[float]]:
    n = len(matrix)
    inverse_cols = [
        _solve_linear_system(matrix, [1.0 if i == j else 0.0 for i in range(n)]) for j in range(n)
    ]
    return [[inverse_cols[j][i] for j in range(n)] for i in range(n)]


def _wald_ci(
    cols: List[List[float]],
    y: List[float],
    coeffs: List[float],
    names: List[str],
    z: float = 1.96,
) -> Optional[Dict[str, List[float]]]:
    """Approximate 95% Wald CI for linear-in-parameters models."""
    n = len(y)
    k = len(coeffs)
    if n <= k or k == 0:
        return None
    y_hat = [sum(c * v for c, v in zip(coeffs, row)) for row in cols]
    ss_res = sum((yi - yh) ** 2 for yi, yh in zip(y, y_hat))
    sigma2 = ss_res / (n - k)
    xtx = [[sum(row[i] * row[j] for row in cols) for j in range(k)] for i in range(k)]
    try:
        inv = _invert(xtx)
    except (ValueError, ZeroDivisionError):
        return None
    out: Dict[str, List[float]] = {}
    for i, name in enumerate(names):
        var = sigma2 * inv[i][i]
        if var < 0 or not math.isfinite(var):
            return None
        se = math.sqrt(var)
        out[name] = [coeffs[i] - z * se, coeffs[i] + z * se]
    return out


def _pack(
    model: str,
    axis: str,
    params: Dict[str, float],
    x: List[float],
    y: List[float],
    y_hat: List[float],
    fn: Callable[[float], float],
    param_ci: Optional[Dict[str, List[float]]] = None,
) -> Dict[str, Any]:
    n = len(y)
    k = max(len(params), 1)
    mean = sum(y) / n
    residuals = [yi - yh for yi, yh in zip(y, y_hat)]
    ss_res = sum(r * r for r in residuals)
    ss_tot = sum((yi - mean) ** 2 for yi in y)
    r2 = 1.0 if ss_tot < 1e-12 else 1.0 - ss_res / ss_tot
    rmse = math.sqrt(ss_res / n) if n else 0.0
    mae = sum(abs(r) for r in residuals) / n if n else 0.0
    aicc = None
    dof = n - k - 1
    if ss_res > 0 and dof > 0:
        aic = n * math.log(ss_res / n) + 2 * k
        aicc = _finite_or_none(aic + 2 * k * (k + 1) / dof)
    return {
        "model": model,
        "label": _label(model, axis),
        "params": params,
        "r2": r2,
        "rmse": rmse,
        "mae": mae,
        "aicc": aicc,
        "n": n,
        "fitted": _sample_curve(x, fn),
        "x_min": min(x),
        "x_max": max(x),
        "extrapolation_forbidden": True,
        "residual_max_abs": max((abs(r) for r in residuals), default=0.0),
        "param_ci": param_ci,
        "loocv_rmse": None,
    }


def _predict(model: str, params: Dict[str, float], x: List[float]) -> Optional[List[float]]:
    try:
        if model == "linear":
            a, b = params["a"], params["b"]
            return [a + b * xi for xi in x]
        if model == "quadratic":
            a, b = params["a"], params["b"]
            c = params["q"] if "q" in params else params["c"]
            return [a + b * xi + c * xi * xi for xi in x]
        if model == "exponential":
            a, b = params["a"], params["b"]
            return [a * math.exp(b * xi) for xi in x]
        if model == "logarithmic":
            a, b = params["a"], params["b"]
            return [a + b * math.log(xi) for xi in x]
        if model == "power":
            a, b = params["a"], params["b"]
            return [a * (xi**b) for xi in x]
        if model == "first_order":
            a, b, k = params["a"], params["b"], params["k"]
            return [a - b * math.exp(-k * xi) for xi in x]
        if model == "arrhenius":
            a, ea = params["a"], params["Ea_kJ_mol"] * 1000.0
            return [a * math.exp(-ea / (GAS_CONST * (xi + 273.15))) for xi in x]
        if model == "kohlrausch":
            a, b, k = params["a"], params["b"], params["K"]
            return [a + b * xi - k * (xi**1.5) for xi in x]
    except (KeyError, OverflowError, ValueError, ZeroDivisionError):
        return None
    return None


LOOCV_MAX_N = 60
LOOCV_SKIP_MODELS = frozenset({"first_order"})


def _loocv_rmse(model: str, x: List[float], y: List[float], axis: str) -> Optional[float]:
    n = len(x)
    if model in LOOCV_SKIP_MODELS or n < 4 or n > LOOCV_MAX_N:
        return None
    fitter = FITTERS.get(model)
    if fitter is None:
        return None
    sq = 0.0
    count = 0
    for i in range(n):
        x_tr = x[:i] + x[i + 1 :]
        y_tr = y[:i] + y[i + 1 :]
        try:
            res = fitter(x_tr, y_tr, axis=axis)
        except (ValueError, OverflowError, ZeroDivisionError):
            res = None
        if not res:
            continue
        pred = _predict(model, res["params"], [x[i]])
        if pred is None:
            continue
        err = y[i] - pred[0]
        sq += err * err
        count += 1
    if count == 0:
        return None
    return math.sqrt(sq / count)


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
    cols = [[1.0, xi] for xi in x]
    return _pack(
        "linear",
        axis,
        {"a": a, "b": b},
        x,
        y,
        y_hat,
        lambda xi: a + b * xi,
        param_ci=_wald_ci(cols, y, [a, b], ["a", "b"]),
    )


def fit_quadratic(x: List[float], y: List[float], axis: str = "time") -> Optional[Dict[str, Any]]:
    if len(x) < 3:
        return None
    a, b, c = _polyfit(x, y, 2)
    y_hat = [a + b * xi + c * xi * xi for xi in x]
    cols = [[1.0, xi, xi * xi] for xi in x]
    qname = "q" if axis == "concentration" else "c"
    return _pack(
        "quadratic",
        axis,
        {"a": a, "b": b, qname: c},
        x,
        y,
        y_hat,
        lambda xi: a + b * xi + c * xi * xi,
        param_ci=_wald_ci(cols, y, [a, b, c], ["a", "b", qname]),
    )


def fit_exponential(x: List[float], y: List[float], axis: str = "time") -> Optional[Dict[str, Any]]:
    """y = a·e^(b·x) → ln(y) = ln(a) + b·x（要求 y>0）。"""
    if len(x) < 2 or any(yi <= 0 for yi in y):
        return None
    ln_y = [math.log(yi) for yi in y]
    ln_a, b = _polyfit(x, ln_y, 1)
    a = math.exp(ln_a)
    y_hat = [a * math.exp(b * xi) for xi in x]
    return _pack(
        "exponential",
        axis,
        {"a": a, "b": b},
        x,
        y,
        y_hat,
        lambda xi: a * math.exp(b * xi),
    )


def fit_logarithmic(x: List[float], y: List[float], axis: str = "time") -> Optional[Dict[str, Any]]:
    """y = a + b·ln(x)（要求 x>0）。"""
    if len(x) < 2 or any(xi <= 0 for xi in x):
        return None
    ln_x = [math.log(xi) for xi in x]
    a, b = _polyfit(ln_x, y, 1)
    y_hat = [a + b * math.log(xi) for xi in x]
    cols = [[1.0, math.log(xi)] for xi in x]
    return _pack(
        "logarithmic",
        axis,
        {"a": a, "b": b},
        x,
        y,
        y_hat,
        lambda xi: a + b * math.log(xi),
        param_ci=_wald_ci(cols, y, [a, b], ["a", "b"]),
    )


def fit_power(x: List[float], y: List[float], axis: str = "time") -> Optional[Dict[str, Any]]:
    """y = a·x^b → ln(y) = ln(a) + b·ln(x)（要求 x>0, y>0）。"""
    if len(x) < 2 or any(xi <= 0 for xi in x) or any(yi <= 0 for yi in y):
        return None
    ln_x = [math.log(xi) for xi in x]
    ln_y = [math.log(yi) for yi in y]
    ln_a, b = _polyfit(ln_x, ln_y, 1)
    a = math.exp(ln_a)
    y_hat = [a * xi**b for xi in x]
    return _pack(
        "power",
        axis,
        {"a": a, "b": b},
        x,
        y,
        y_hat,
        lambda xi: a * xi**b,
    )


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
        ss = sum((yi - yh) ** 2 for yi, yh in zip(y, y_hat))
        if best is None or ss < best["_ss"]:
            best = {"a": a, "b": b, "k": k, "y_hat": y_hat, "_ss": ss}
    if best is None:
        return None
    a, b, k = best["a"], best["b"], best["k"]
    return _pack(
        "first_order",
        axis,
        {"a": a, "b": b, "k": k},
        x,
        y,
        best["y_hat"],
        lambda xi: a - b * math.exp(-k * xi),
    )


def fit_arrhenius(x: List[float], y: List[float], axis: str = "temperature") -> Optional[Dict[str, Any]]:
    """Arrhenius：y = a·e^(−Ea/(R·T))，T = x + 273.15（x 为 °C）。

    线性化：ln(y) = ln(a) − (Ea/R)·(1/T)，对 1/T 做线性拟合，
    斜率 = −Ea/R，据此输出活化能 Ea(kJ/mol)。要求 y>0。
    """
    if len(x) < 3 or any(yi <= 0 for yi in y):
        return None
    # 至少需要 3 个不同温度且覆盖 1 °C；恒温/近恒温数据无法稳定识别活化能。
    if len(set(x)) < 3 or max(x) - min(x) < ARRHENIUS_MIN_TEMPERATURE_SPAN_C:
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
    return _pack(
        "arrhenius",
        axis,
        {"a": a, "Ea_kJ_mol": ea / 1000.0},
        x,
        y,
        y_hat,
        lambda xi: a * math.exp(-ea / (GAS_CONST * (xi + 273.15))),
    )


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
    cols = [[1.0, xi, -(xi**1.5)] for xi in x]
    return _pack(
        "kohlrausch",
        axis,
        {"a": a, "b": b, "K": k},
        x,
        y,
        y_hat,
        lambda xi: a + b * xi - k * xi**1.5,
        param_ci=_wald_ci(cols, y, [a, b, k], ["a", "b", "K"]),
    )


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
    # None 表示“使用该轴全部模型”；显式 [] 表示“不运行任何模型”。
    selected = list(available.keys()) if models is None else models
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
            res["loocv_rmse"] = _loocv_rmse(name, x, y, x_axis)
            results.append(res)
    results.sort(key=lambda r: -r["r2"])
    return results
