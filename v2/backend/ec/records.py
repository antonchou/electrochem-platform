"""历史数据的读取与分析：帧 → 数据点、判稳、CSV 导出、浓度/温度分析、标定点计算。

帧表只存原始量；这里用测量的参数快照（Kcell、α）现算 G/κ(T)/κ25，与实时推送用的是同一个函数。
"""

from __future__ import annotations

import csv
import io
import math
from typing import Any, Iterable, Sequence

from .chemistry import derive
from .errors import Invalid, NotFound
from .fitting import FitError, cell_constant_fit, concentration_fit, temperature_fit
from .frames import HARD_FLAGS, split_flags
from .qc import QcConfig, QcPoint, QcResult, assess
from .store import FrameRow, Store

CSV_COLUMNS = (
    "seq", "t_s", "timestamp_utc", "device_seq", "device_ms",
    "voltage_v", "current_a", "temperature_c",
    "conductance_s", "kappa_t_us_cm", "kappa25_us_cm", "flags",
)


def make_point(
    t_s: float,
    voltage_v: float | None,
    current_a: float | None,
    temperature_c: float | None,
    flags: tuple[str, ...],
    cell_constant_per_cm: float,
    alpha_per_c: float,
    seq: int | None = None,
) -> dict[str, Any]:
    derived = derive(voltage_v, current_a, temperature_c, cell_constant_per_cm, alpha_per_c)
    if derived.polarity_error:
        flags = flags + ("POLARITY",)
    return {
        "seq": seq,
        "t_s": t_s,
        "voltage_v": voltage_v,
        "current_a": current_a,
        "temperature_c": temperature_c,
        "conductance_s": derived.conductance_s,
        "kappa_t_us_cm": derived.kappa_t_us_cm,
        "kappa25_us_cm": derived.kappa25_us_cm,
        "flags": list(flags),
    }


def row_point(row: FrameRow, measurement: dict[str, Any]) -> dict[str, Any]:
    return make_point(
        row.t_s, row.voltage_v, row.current_a, row.temperature_c, split_flags(row.flags),
        measurement["cell_constant_per_cm"], measurement["alpha_per_c"], seq=row.seq,
    )


def qc_point(point: dict[str, Any]) -> QcPoint:
    return QcPoint(point["t_s"], point["kappa25_us_cm"], point["temperature_c"], tuple(point["flags"]))


def require_measurement(store: Store, measurement_id: int) -> dict[str, Any]:
    measurement = store.get_measurement(measurement_id)
    if measurement is None:
        raise NotFound(f"测量 #{measurement_id} 不存在")
    return measurement


def measurement_points(store: Store, measurement_id: int, max_points: int) -> dict[str, Any]:
    """全部帧的数据点；超过 max_points 时等间隔抽样（保留最后一点）。"""
    measurement = require_measurement(store, measurement_id)
    points = [row_point(row, measurement) for row in store.frames(measurement_id)]
    total = len(points)
    step = max(1, math.ceil(total / max_points))
    if step > 1:
        sampled = points[::step]
        if sampled[-1] is not points[-1]:
            sampled.append(points[-1])
        points = sampled
    return {"measurement_id": measurement_id, "total": total, "step": step, "points": points}


def assess_measurement(store: Store, measurement: dict[str, Any], config: QcConfig) -> QcResult:
    rows = store.frames(measurement["id"])
    return assess([qc_point(row_point(row, measurement)) for row in rows], config)


def export_csv(store: Store, measurement_id: int) -> tuple[str, str]:
    measurement = require_measurement(store, measurement_id)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for row in store.frames(measurement_id):
        point = row_point(row, measurement)
        writer.writerow([
            row.seq, row.t_s, row.timestamp_utc, row.device_seq, row.device_ms,
            row.voltage_v, row.current_a, row.temperature_c,
            point["conductance_s"], point["kappa_t_us_cm"], point["kappa25_us_cm"], "|".join(point["flags"]),
        ])
    return f"measurement_{measurement_id}.csv", out.getvalue()


def representative(measurement: dict[str, Any]) -> float | None:
    qc = measurement.get("qc") or {}
    return qc.get("representative_kappa25")


def _completed_with_value(store: Store, measurement_id: int) -> tuple[dict[str, Any], float]:
    measurement = require_measurement(store, measurement_id)
    if measurement["status"] != "completed":
        raise Invalid(f"测量 #{measurement_id} 没有正常结束（{measurement['status']}）")
    value = representative(measurement)
    if value is None:
        raise Invalid(f"测量 #{measurement_id} 判稳未通过，没有代表值")
    return measurement, value


def _unique(ids: Iterable[int]) -> list[int]:
    ids = list(ids)
    if len(set(ids)) != len(ids):
        raise Invalid("同一次测量不能重复选择")
    return ids


def concentration_analysis(store: Store, measurement_ids: Sequence[int]) -> dict[str, Any]:
    ids = _unique(measurement_ids)
    if len(ids) < 2:
        raise Invalid("浓度拟合至少需要 2 次测量")
    points = []
    for measurement_id in ids:
        measurement, value = _completed_with_value(store, measurement_id)
        if measurement["concentration_mmol_l"] is None:
            raise Invalid(f"测量 #{measurement_id}（{measurement['sample_name']}）没有填浓度")
        points.append({
            "measurement_id": measurement_id,
            "sample_name": measurement["sample_name"],
            "concentration_mmol_l": measurement["concentration_mmol_l"],
            "kappa25_us_cm": value,
        })
    fit = concentration_fit([(p["concentration_mmol_l"], p["kappa25_us_cm"]) for p in points])
    return {"points": points, **fit}


def temperature_analysis(store: Store, measurement_id: int) -> dict[str, Any]:
    measurement = require_measurement(store, measurement_id)
    pairs = []
    for row in store.frames(measurement_id):
        point = row_point(row, measurement)
        if point["kappa_t_us_cm"] is None or point["temperature_c"] is None:
            continue
        if HARD_FLAGS.intersection(point["flags"]):
            continue
        pairs.append((point["temperature_c"], point["kappa_t_us_cm"]))
    return {"measurement_id": measurement_id, **temperature_fit(pairs)}


def calibration_points(store: Store, entries: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """标定：每个标准液点取该次测量的代表 κ25 反推 G25 = κ25 / Kcell快照，再过原点拟合求 Kcell。"""
    if not entries:
        raise Invalid("至少需要 1 个标准液点")
    _unique(entry["measurement_id"] for entry in entries)
    rows = []
    for entry in entries:
        measurement, value = _completed_with_value(store, entry["measurement_id"])
        rows.append({
            "measurement_id": measurement["id"],
            "standard_name": entry["standard_name"],
            "standard_kappa25_us_cm": entry["standard_kappa25_us_cm"],
            "conductance25_s": value * 1e-6 / measurement["cell_constant_per_cm"],
            "verdict": measurement["qc"]["verdict"],
        })
    try:
        fit = cell_constant_fit([(row["conductance25_s"], row["standard_kappa25_us_cm"]) for row in rows])
    except FitError as exc:
        raise Invalid(f"无法求电池常数：{exc}") from exc
    for row, deviation in zip(rows, fit["deviations_pct"]):
        row["deviation_pct"] = deviation
    return fit, rows
