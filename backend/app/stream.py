"""模拟数据发生器：模拟电极 I–V 链路原始读数（基值 1413 μS/cm + 漂移 + 噪声）。

只生成原始 U/I/T；G/κ(T)/κ25 由软件计算层（app.calibration）还原，
与 MockDevice / 未来硬件走同一条计算链。供调试接口 /api/debug/burst 使用。
"""

import datetime
import math
import random
import time

from . import calibration

BASE_EC = 1413.0
BASE_TEMP = 25.0
EXCITATION_AMPLITUDE_V = 0.4
KCELL_CM_INV = 1.0
ALPHA_PER_C = 0.02
COMPENSATION_MODEL = "linear"
EXCITATION_FREQUENCY_HZ = 1000.0
RANGE_ID = "R_100R_10K"
SENSOR_PATH_ID = "EC_IV_CELL_DEBUG"
CALIBRATION_ID = "CAL_DEBUG_GENERATOR"
DEBUG_BURST_UID_PREFIX = "DEBUG-BURST"


def generate_frame(t: float, *, experiment_uid: str = DEBUG_BURST_UID_PREFIX) -> dict:
    """生成严格 V2 帧：Raw U/I/T + Calibrated/Derived/Trace/Quality。"""
    drift = math.sin(t / 30.0) * 6.0
    noise = (random.random() - 0.5) * 3.0
    target_k25 = BASE_EC + drift + noise
    temperature = round(BASE_TEMP + (random.random() - 0.5) * 0.3, 2)

    # 由目标 κ25 先还原 κ(T)，再反推等效阻抗与电流（同 MockDevice.read）
    voltage = round(EXCITATION_AMPLITUDE_V, 4)
    target_kappa_t = target_k25 * (1.0 + ALPHA_PER_C * (temperature - 25.0))
    resistance = KCELL_CM_INV / (target_kappa_t * 1e-6)
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
        "message_type": "measurement",
        "schema_version": "2.0",
        "seq_no": int(round(t * 10.0)) + 1,
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
        "monotonic_ms": int(time.monotonic() * 1000),
        "t_seconds": round(t, 2),
        "status": "running",
        "experiment_uid": experiment_uid,
        "voltage_raw_v": voltage,
        "current_raw_a": current,
        "temperature_raw_c": temperature,
        "voltage_cal_v": None,
        "current_cal_a": None,
        "conductance_s": computed["conductance_s"],
        "kappa_t_us_cm": computed["kappa_t_us_cm"],
        "kappa_25_us_cm": computed["kappa_25_us_cm"],
        "excitation_frequency_hz": EXCITATION_FREQUENCY_HZ,
        "excitation_amplitude_v": EXCITATION_AMPLITUDE_V,
        "range_id": RANGE_ID,
        "sensor_path_id": SENSOR_PATH_ID,
        "calibration_id": CALIBRATION_ID,
        "cell_constant_cm_inv": KCELL_CM_INV,
        "calibration_valid_until_utc": None,
        "compensation_model": COMPENSATION_MODEL,
        "alpha_per_c": ALPHA_PER_C,
        # 调试帧使用独立 trace namespace；前端可据此避免把它误认为新实验边界。
        "quality_flags": [
            "SIMULATED",
            "DEBUG_BURST",
            *computed["quality_flags"],
        ],
    }
    return frame
