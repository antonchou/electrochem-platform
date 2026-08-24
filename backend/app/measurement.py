"""I–V 测量计算链（REQ-M-001 软件侧）。

由原始电压/电流/温度计算电导与电导率（原路线图 §2）：
    G(T)     = I / U
    κ(T)     = Kcell × G(T)
    κ25      = κ(T) / [1 + α × (T - 25)]

单位约定：U[V], I[A], G[S], Kcell[cm⁻¹], κ[μS/cm]。
1 S/cm = 1e6 μS/cm，故 κ(T)[μS/cm] = Kcell × G × 1e6。
本模块为纯函数，供 mock 仿真与真实硬件共用；不依赖硬件驱动。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    conductance_s: float      # G(T) = I/U
    kappa_t_us_cm: float      # κ(T) = Kcell·G(T)
    kappa_25_us_cm: float     # κ25，温补折算后


def compute_chain(
    voltage_v: float,
    current_a: float,
    temperature_c: float,
    cell_constant_per_cm: float,
    alpha_per_c: float,
) -> MeasurementResult:
    """由 U/I/T 计算 G、κ(T)、κ25。输入非法抛 ValueError。

    参数校验（原路线图 §3：激励幅值/量程为台架选型项，不得以器件宣传参数冻结）：
    - voltage_v 必须为正（0/负电压无法定义电导）
    - cell_constant_per_cm 必须为正
    - 温补分母 1 + α·(T-25) 不得为 0（α 或 T 导致分母归零时无法温补）
    """
    if not math.isfinite(voltage_v) or not math.isfinite(current_a) or not math.isfinite(temperature_c):
        raise ValueError("voltage_v, current_a and temperature_c must be finite")
    if not math.isfinite(cell_constant_per_cm) or not math.isfinite(alpha_per_c):
        raise ValueError("cell_constant_per_cm and alpha_per_c must be finite")
    if not voltage_v > 0:
        raise ValueError("voltage_v must be positive")
    if not cell_constant_per_cm > 0:
        raise ValueError("cell_constant_per_cm must be positive")
    denominator = 1.0 + alpha_per_c * (temperature_c - 25.0)
    if abs(denominator) < 1e-12:
        raise ValueError(
            "temperature compensation denominator is zero "
            f"(alpha={alpha_per_c}, T={temperature_c}); cannot compute kappa25"
        )

    conductance_s = current_a / voltage_v
    kappa_t_us_cm = cell_constant_per_cm * conductance_s * 1e6
    kappa_25_us_cm = kappa_t_us_cm / denominator
    return MeasurementResult(
        conductance_s=conductance_s,
        kappa_t_us_cm=kappa_t_us_cm,
        kappa_25_us_cm=kappa_25_us_cm,
    )
