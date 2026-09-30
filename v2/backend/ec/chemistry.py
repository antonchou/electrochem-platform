"""电导率计算链与溶液数据。全平台只有这一处计算 G / κ(T) / κ25。

单位：U [V]，I [A]，G [S]，Kcell [cm⁻¹]，κ [µS/cm]，T [°C]，浓度 [mmol/L]。
κ [S/cm] = Kcell · G，所以 κ [µS/cm] = Kcell · G · 1e6。
摩尔电导率 Λm [S·cm²/mol] = κ [µS/cm] / c [mmol/L]（两边的 1e-6 正好约掉）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

REFERENCE_TEMPERATURE_C = 25.0


def finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


@dataclass(frozen=True, slots=True)
class Derived:
    conductance_s: float | None = None
    kappa_t_us_cm: float | None = None
    kappa25_us_cm: float | None = None
    polarity_error: bool = False  # U ≤ 0 或 I < 0：接线反了，G 无定义


def derive(
    voltage_v: float | None,
    current_a: float | None,
    temperature_c: float | None,
    cell_constant_per_cm: float,
    alpha_per_c: float,
) -> Derived:
    """由原始 U/I/T 和测量参数算 G、κ(T)、κ25；算不出的量为 None，从不抛异常。"""
    if not (finite(voltage_v) and finite(current_a)):
        return Derived()
    if voltage_v <= 0 or current_a < 0:
        return Derived(polarity_error=True)
    conductance = current_a / voltage_v
    kappa_t = cell_constant_per_cm * conductance * 1e6
    if not (math.isfinite(conductance) and math.isfinite(kappa_t)):
        return Derived()  # U 极小导致溢出
    kappa25 = None
    factor = compensation_factor(temperature_c, alpha_per_c)
    if factor is not None:
        kappa25 = kappa_t / factor
    return Derived(conductance, kappa_t, kappa25)


def compensation_factor(temperature_c: float | None, alpha_per_c: float) -> float | None:
    """线性温补因子 1 + α·(T − 25)；温度缺失或因子 ≤ 0（远超适用温区）时为 None。"""
    if not finite(temperature_c):
        return None
    factor = 1.0 + alpha_per_c * (temperature_c - REFERENCE_TEMPERATURE_C)
    return factor if factor > 0 else None


@dataclass(frozen=True, slots=True)
class Standard:
    name: str
    kappa25_us_cm: float


# 常用电导率标准液（25 °C 标称值）。实际标定以瓶签标称值与批次为准，界面允许自填。
STANDARDS = (
    Standard("84 µS/cm 标准液", 84.0),
    Standard("KCl 0.001 mol/L", 147.0),
    Standard("KCl 0.01 mol/L", 1413.0),
    Standard("KCl 0.1 mol/L", 12880.0),
)

# KCl 25 °C 摩尔电导率 Λm [S·cm²/mol] 随浓度 c [mol/L]（CRC Handbook）。
# 只给模拟器生成与浓度相符的读数，不参与任何测量计算。
_KCL_MOLAR_CONDUCTIVITY = (
    (0.0, 149.79),
    (0.0005, 147.74),
    (0.001, 146.88),
    (0.005, 143.48),
    (0.01, 141.20),
    (0.02, 138.27),
    (0.05, 133.30),
    (0.1, 128.90),
)


def kcl_kappa25_us_cm(concentration_mmol_l: float) -> float:
    """KCl 溶液 25 °C 电导率（µS/cm）：在 √c 上对表插值，0.1 mol/L 以上按最后一段外推。"""
    if concentration_mmol_l <= 0:
        return 0.0
    c = concentration_mmol_l / 1000.0
    x = math.sqrt(c)
    table = _KCL_MOLAR_CONDUCTIVITY
    for (c0, l0), (c1, l1) in zip(table, table[1:]):
        if c <= c1:
            break
    x0, x1 = math.sqrt(c0), math.sqrt(c1)
    molar = l0 + (l1 - l0) * (x - x0) / (x1 - x0)
    return max(molar, 1.0) * concentration_mmol_l  # 远超表格的浓度外推会变负，兜底
