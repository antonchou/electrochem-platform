"""模拟数据发生器：模拟一块稳定电导率探头的读数（基值 1413 μS/cm + 轻微漂移 + 噪声）。"""

import math
import random

BASE_EC = 1413.0
BASE_TEMP = 25.0


def generate_frame(t: float) -> dict:
    """生成一帧（与前端约定的协议字段）。"""
    drift = math.sin(t / 30.0) * 6.0
    noise = (random.random() - 0.5) * 3.0
    return {
        "timestamp": round(t, 2),
        "ec": round(BASE_EC + drift + noise, 1),
        "temperature": round(BASE_TEMP + (random.random() - 0.5) * 0.3, 2),
        "status": "running",
    }
