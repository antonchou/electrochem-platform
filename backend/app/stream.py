"""模拟数据发生器：模拟电极 I–V 链路原始读数（基值 1413 μS/cm + 漂移 + 噪声）。

只生成原始 U/I/T；G/κ(T)/κ25 由软件计算层（app.calibration）还原，
与 MockDevice / 未来硬件走同一条计算链。供调试接口 /api/debug/burst 使用。
"""

import math
import random

from . import calibration

BASE_EC = 1413.0
BASE_TEMP = 25.0
EXCITATION_AMPLITUDE_V = 0.4
KCELL_CM_INV = 1.0
ALPHA_PER_C = 0.02
COMPENSATION_MODEL = "linear"


def generate_frame(t: float) -> dict:
    """生成一帧（V2 协议字段：Raw U/I/T + Calibrated/Derived，含 V1 兼容别名）。"""
    drift = math.sin(t / 30.0) * 6.0
    noise = (random.random() - 0.5) * 3.0
    target_k25 = BASE_EC + drift + noise
    temperature = round(BASE_TEMP + (random.random() - 0.5) * 0.3, 2)

    # 由目标 κ25 反推等效阻抗与电流（同 MockDevice.read）
    voltage = round(EXCITATION_AMPLITUDE_V, 4)
    resistance = KCELL_CM_INV / (target_k25 * 1e-6)
    current = round(voltage / resistance, 9)

    computed = calibration.compute_iv(
        voltage_raw_v=voltage,
        current_raw_a=current,
        temperature_raw_c=temperature,
        cell_constant_cm_inv=KCELL_CM_INV,
        alpha_per_c=ALPHA_PER_C,
        compensation_model=COMPENSATION_MODEL,
    )

    frame: dict = {
        "schema_version": "2.0",
        "timestamp": round(t, 2),
        "status": "running",
        "voltage_raw_v": voltage,
        "current_raw_a": current,
        "temperature_raw_c": temperature,
        "temperature": temperature,  # V1 废弃别名
        "excitation_amplitude_v": EXCITATION_AMPLITUDE_V,
        "compensation_model": COMPENSATION_MODEL,
        "alpha_per_c": ALPHA_PER_C,
    }
    if computed["conductance_s"] is not None:
        frame["conductance_s"] = computed["conductance_s"]
    if computed["kappa_t_us_cm"] is not None:
        frame["kappa_t_us_cm"] = computed["kappa_t_us_cm"]
        frame["ec"] = computed["kappa_t_us_cm"]  # V1 废弃别名
    if computed["kappa_25_us_cm"] is not None:
        frame["kappa_25_us_cm"] = computed["kappa_25_us_cm"]
    frame["quality_flags"] = list(computed["quality_flags"]) or ["SIMULATED"]
    return frame
