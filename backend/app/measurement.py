"""电极 I–V 测量纯物理计算层（SRS v0.2 §2 / §5.1，路线文档 §2）。

只做物理换算，不涉及校准策略：
    G(T)  = I / U                     电导，单位 S
    κ(T)  = Kcell · G(T) · 1e6        实测温度电导率，单位 μS/cm

约定：
- 输入缺失 / 非有限 / U≈0（开路、无激励）→ 返回 None，绝不静默产生伪精确值；
- 是否允许输出由调用方（app.calibration）按校准状态决定；
- 纯 Python，不引入 numpy，保持树莓派轻量。
"""

from __future__ import annotations

import math

from typing import Optional

# S/cm → μS/cm 换算系数：1 S = 10^6 μS
S_TO_US = 1_000_000.0


def _finite(value: float) -> bool:
    return math.isfinite(value)


def conductance(
    current_raw_a: Optional[float],
    voltage_raw_v: Optional[float],
) -> Optional[float]:
    """G = I / U，单位 S。U 必须非零（U≈0 为开路/无激励，无法定义电导）。"""
    if current_raw_a is None or voltage_raw_v is None:
        return None
    if not _finite(current_raw_a) or not _finite(voltage_raw_v):
        return None
    if voltage_raw_v == 0.0:
        return None
    return current_raw_a / voltage_raw_v


def kappa_t(
    conductance_s: Optional[float],
    cell_constant_cm_inv: Optional[float],
) -> Optional[float]:
    """κ(T) = Kcell · G · 1e6，单位 μS/cm。Kcell 为 None/负值视为未校准。"""
    if conductance_s is None or cell_constant_cm_inv is None:
        return None
    if not _finite(conductance_s) or not _finite(cell_constant_cm_inv):
        return None
    if cell_constant_cm_inv < 0:
        return None
    return conductance_s * cell_constant_cm_inv * S_TO_US
