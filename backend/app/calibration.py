"""校准与温度补偿层（SRS v0.2 §2 / §5.1 / §5.4）。

语义（未校准时禁止伪造正式电导率）：
- 无有效 Kcell：U/I/T 照常保存；G 可计算；κ(T) 为空并标记 UNCALIBRATED；κ25 必须为空。
- 有 Kcell 但温补不可用（模型不适 / α 无效 / 温度无效 / 校准已过期）：
  κ(T) 有值；κ25 为空。
- 完整有效校准：κ(T)、κ25 都有值。

质量标志（Quality 层）：
    UNCALIBRATED / CALIBRATION_EXPIRED / TEMPERATURE_INVALID /
    COMPENSATION_UNAVAILABLE / OPEN_CIRCUIT / SATURATED / UNDER_RANGE
"""

from __future__ import annotations

import math
from typing import Optional

from . import measurement

# ---- 质量标志 ----
UNCALIBRATED = "UNCALIBRATED"
CALIBRATION_EXPIRED = "CALIBRATION_EXPIRED"
TEMPERATURE_INVALID = "TEMPERATURE_INVALID"
COMPENSATION_UNAVAILABLE = "COMPENSATION_UNAVAILABLE"
OPEN_CIRCUIT = "OPEN_CIRCUIT"
SATURATED = "SATURATED"
UNDER_RANGE = "UNDER_RANGE"

# 参考温度与温度有效范围（SRS v0.2 §4：温度范围 10–40°C 强制）
REFERENCE_TEMPERATURE_C = 25.0
VALID_TEMPERATURE_RANGE_C = (10.0, 40.0)

# 支持的温补模型：linear（α·(T−25) 线性）；none（不补偿，κ25=κ(T)）
VALID_COMPENSATION_MODELS = frozenset({"linear", "none"})
DEFAULT_COMPENSATION_MODEL = "linear"
# 线性温补默认 α（NaCl 25°C 附近经验值，仅限已验证范围）
DEFAULT_ALPHA_PER_C = 0.02


def _finite(value: float) -> bool:
    return math.isfinite(value)


def _temperature_valid(temperature_raw_c: Optional[float]) -> bool:
    if temperature_raw_c is None or not _finite(temperature_raw_c):
        return False
    lo, hi = VALID_TEMPERATURE_RANGE_C
    return lo <= temperature_raw_c <= hi


def kappa_25(
    kappa_t_us_cm: Optional[float],
    temperature_raw_c: Optional[float],
    alpha_per_c: Optional[float],
    compensation_model: str = DEFAULT_COMPENSATION_MODEL,
) -> Optional[float]:
    """κ25 = κ(T) / [1 + α·(T − 25)]（linear），或直接返回 κ(T)（none）。

    非法输入（T 无效 / α 非有限 / 模型不支持）返回 None，不产生伪精确值。
    """
    if kappa_t_us_cm is None or not _finite(kappa_t_us_cm):
        return None
    if not _temperature_valid(temperature_raw_c):
        return None
    if compensation_model == "none":
        return kappa_t_us_cm
    if compensation_model != "linear":
        return None
    if (
        alpha_per_c is None
        or not _finite(alpha_per_c)
        or not 0.0 <= alpha_per_c < 1.0 / 15.0
    ):
        return None
    denominator = 1.0 + alpha_per_c * (temperature_raw_c - REFERENCE_TEMPERATURE_C)
    if denominator <= 1e-12:
        return None
    return kappa_t_us_cm / denominator


def compute_iv(
    *,
    voltage_raw_v: Optional[float],
    current_raw_a: Optional[float],
    temperature_raw_c: Optional[float],
    cell_constant_cm_inv: Optional[float],
    alpha_per_c: Optional[float] = None,
    compensation_model: str = DEFAULT_COMPENSATION_MODEL,
    calibration_expired: bool = False,
    extra_flags: tuple[str, ...] = (),
) -> dict:
    """编排整条计算链：U/I/T → G → κ(T) → κ25，并产出质量标志。

    返回：
        conductance_s / kappa_t_us_cm / kappa_25_us_cm（可空）
        quality_flags: tuple[str, ...]
    """
    result = {
        "conductance_s": None,
        "kappa_t_us_cm": None,
        "kappa_25_us_cm": None,
    }
    flags: list[str] = list(extra_flags)

    g = measurement.conductance(current_raw_a, voltage_raw_v)
    if g is None:
        # 区分无法定义电导的原因（零激励 vs 缺测）
        if voltage_raw_v is not None and voltage_raw_v == 0.0:
            flags.append(OPEN_CIRCUIT)
        return {**result, "quality_flags": tuple(flags)}
    result["conductance_s"] = g

    # 无有效 Kcell：只能给 G，κ(T)/κ25 不得伪造
    if cell_constant_cm_inv is None or not _finite(cell_constant_cm_inv):
        flags.append(UNCALIBRATED)
        return {**result, "quality_flags": tuple(flags)}
    if cell_constant_cm_inv < 0:
        flags.append(UNCALIBRATED)
        return {**result, "quality_flags": tuple(flags)}

    kt = measurement.kappa_t(g, cell_constant_cm_inv)
    if kt is None:
        return {**result, "quality_flags": tuple(flags)}
    result["kappa_t_us_cm"] = kt

    # 校准过期：κ(T) 保留（已按 Kcell 计算），但 κ25 不再输出
    if calibration_expired:
        flags.append(CALIBRATION_EXPIRED)
        return {**result, "quality_flags": tuple(flags)}

    # 温度无效 / 温补模型不适：κ25 必须为空
    if not _temperature_valid(temperature_raw_c):
        flags.append(TEMPERATURE_INVALID)
        return {**result, "quality_flags": tuple(flags)}
    if compensation_model not in VALID_COMPENSATION_MODELS:
        flags.append(COMPENSATION_UNAVAILABLE)
        return {**result, "quality_flags": tuple(flags)}
    if compensation_model == "linear" and (
        alpha_per_c is None
        or not _finite(alpha_per_c)
        or not 0.0 <= alpha_per_c < 1.0 / 15.0
    ):
        flags.append(COMPENSATION_UNAVAILABLE)
        return {**result, "quality_flags": tuple(flags)}

    k25 = kappa_25(kt, temperature_raw_c, alpha_per_c, compensation_model)
    if k25 is None:
        flags.append(COMPENSATION_UNAVAILABLE)
        return {**result, "quality_flags": tuple(flags)}
    result["kappa_25_us_cm"] = k25
    return {**result, "quality_flags": tuple(flags)}
