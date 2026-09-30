"""设备帧：数据结构、固件 JSON 帧解析、设备序号检查。

固件（firmware/esp32_micropython/main.py）每帧输出一行 JSON：
    {"schema_version":2,"seq_no":1,"monotonic_ms":0,"voltage_raw_v":0.1,"current_raw_a":1e-4,
     "temperature_raw_c":25.06,"quality_flags":null,"device_id":"ESP32-IV-3C71BF",
     "firmware_version":"0.1.0-mpy","range_id":"RS1000R_G1",
     "excitation_frequency_hz":10.0,"excitation_amplitude_v":0.2}
以 "# " 开头的行是设备日志。串口设备与回放设备共用这里的解析。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

# 窗口内出现即判 FAIL 的硬异常。POLARITY 由主机按 U/I 符号判定（见 chemistry.derive）。
HARD_FLAGS = frozenset({"SATURATED", "OPEN_CIRCUIT", "SHORT_CIRCUIT", "DROPOUT", "POLARITY"})
# 窗口内出现降为 WARN 的提示。
SOFT_FLAGS = frozenset({"WAVEFORM_UNSTABLE"})
# 主机侧按设备序号给出的溯源标志：只记录，不影响判稳。
SEQ_GAP = "SEQ_GAP"
DEVICE_RESTART = "DEVICE_RESTART"


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    device_id: str | None = None
    firmware_version: str | None = None
    range_id: str | None = None
    excitation_frequency_hz: float | None = None
    excitation_amplitude_v: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "firmware_version": self.firmware_version,
            "range_id": self.range_id,
            "excitation_frequency_hz": self.excitation_frequency_hz,
            "excitation_amplitude_v": self.excitation_amplitude_v,
        }


@dataclass(frozen=True, slots=True)
class Reading:
    """设备的一帧原始读数。缺失或非法的量为 None。"""

    voltage_v: float | None
    current_a: float | None
    temperature_c: float | None
    flags: tuple[str, ...] = ()
    device_seq: int | None = None
    device_ms: int | None = None
    info: DeviceInfo = field(default_factory=DeviceInfo)


class FrameError(ValueError):
    """这一行不是合法的设备帧。"""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def split_flags(raw: str | Iterable[str] | None) -> tuple[str, ...]:
    """'A|B' 或 ['A','B'] → ('A','B')，去空、去重、保序。"""
    if raw is None:
        return ()
    parts = raw.split("|") if isinstance(raw, str) else raw
    out: list[str] = []
    for part in parts:
        token = str(part).strip()
        if token and token not in out:
            out.append(token)
    return tuple(out)


def join_flags(flags: Iterable[str]) -> str | None:
    """('A','B') → 'A|B'；无标志为 None（落库为 NULL）。"""
    tokens = split_flags(list(flags))
    return "|".join(tokens) if tokens else None


def parse_frame(obj: Any) -> Reading:
    """固件帧（已解析的 JSON 对象）→ Reading。缺 seq_no 或三个原始量字段都没有的对象不是帧。"""
    if not isinstance(obj, dict):
        raise FrameError("frame must be a JSON object")
    if "seq_no" not in obj or not any(
        key in obj for key in ("voltage_raw_v", "current_raw_a", "temperature_raw_c")
    ):
        raise FrameError("object lacks seq_no / raw measurement fields")
    raw_flags = obj.get("quality_flags")
    return Reading(
        voltage_v=_number(obj.get("voltage_raw_v")),
        current_a=_number(obj.get("current_raw_a")),
        temperature_c=_number(obj.get("temperature_raw_c")),
        flags=split_flags(raw_flags if isinstance(raw_flags, (str, list)) else None),
        device_seq=_integer(obj.get("seq_no")),
        device_ms=_integer(obj.get("monotonic_ms")),
        info=DeviceInfo(
            device_id=_text(obj.get("device_id")),
            firmware_version=_text(obj.get("firmware_version")),
            range_id=_text(obj.get("range_id")),
            excitation_frequency_hz=_number(obj.get("excitation_frequency_hz")),
            excitation_amplitude_v=_number(obj.get("excitation_amplitude_v")),
        ),
    )


def parse_line(line: str) -> tuple[str, Any]:
    """串口/采集文件的一行 → ("frame", Reading) | ("log", 文本) | ("noise", 文本) | ("empty", None)。"""
    text = line.strip()
    if not text:
        return "empty", None
    if text.startswith("#"):
        return "log", text.lstrip("#").strip()
    if text.startswith("{"):
        try:
            return "frame", parse_frame(json.loads(text))
        except (ValueError, FrameError):
            pass
    return "noise", text


class SequenceCheck:
    """按设备序号检出串口丢行（跳号）和设备重启（序号回退）。"""

    def __init__(self) -> None:
        self._last: int | None = None

    def check(self, device_seq: int | None) -> tuple[str, ...]:
        if device_seq is None:
            return ()
        last, self._last = self._last, device_seq
        if last is None:
            return ()
        if device_seq <= last:
            return (DEVICE_RESTART,)
        if device_seq > last + 1:
            return (SEQ_GAP,)
        return ()
