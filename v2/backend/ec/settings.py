"""全部配置都从环境变量读，只在这里读、只在启动时读一次；非法值直接报错并指明变量名。"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .qc import QcConfig

V2_ROOT = Path(__file__).resolve().parents[2]
DEVICE_KINDS = ("sim", "serial", "replay")


class SettingsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path = V2_ROOT / "data" / "ec.db"
    static_dir: Path = V2_ROOT / "frontend" / "dist"
    host: str = "127.0.0.1"
    port: int = 8000
    device: str = "sim"
    serial_port: str = "/dev/ttyUSB0"
    serial_baud: int = 115200
    replay_path: Path | None = None
    replay_speed: float = 1.0
    replay_loop: bool = False
    sim_rate_hz: float = 2.0
    sim_seed: int | None = None
    sim_settle_s: float = 1.0
    nominal_cell_constant_per_cm: float = 1.0  # 没有标定时使用的标称 Kcell
    alpha_per_c: float = 0.02  # 线性温补系数
    qc: QcConfig = field(default_factory=QcConfig)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        reader = _Reader(env)
        defaults = cls()
        device = reader.text("EC_DEVICE", defaults.device)
        if device not in DEVICE_KINDS:
            raise SettingsError(f"EC_DEVICE={device!r}: expected one of {', '.join(DEVICE_KINDS)}")
        replay_path = reader.text("EC_REPLAY_PATH", "")
        if device == "replay" and not replay_path:
            raise SettingsError("EC_REPLAY_PATH is required when EC_DEVICE=replay")
        return cls(
            db_path=Path(reader.text("EC_DB_PATH", str(defaults.db_path))),
            static_dir=Path(reader.text("EC_STATIC_DIR", str(defaults.static_dir))),
            host=reader.text("EC_HOST", defaults.host),
            port=reader.integer("EC_PORT", defaults.port, minimum=1),
            device=device,
            serial_port=reader.text("EC_SERIAL_PORT", defaults.serial_port),
            serial_baud=reader.integer("EC_SERIAL_BAUD", defaults.serial_baud, minimum=1),
            replay_path=Path(replay_path) if replay_path else None,
            replay_speed=reader.positive("EC_REPLAY_SPEED", defaults.replay_speed),
            replay_loop=reader.flag("EC_REPLAY_LOOP", defaults.replay_loop),
            sim_rate_hz=reader.positive("EC_SIM_RATE_HZ", defaults.sim_rate_hz),
            sim_seed=reader.integer("EC_SIM_SEED", None),
            sim_settle_s=reader.positive("EC_SIM_SETTLE_S", defaults.sim_settle_s),
            nominal_cell_constant_per_cm=reader.positive("EC_CELL_CONSTANT", defaults.nominal_cell_constant_per_cm),
            alpha_per_c=reader.number("EC_ALPHA", defaults.alpha_per_c),
            qc=QcConfig(window_s=reader.positive("EC_QC_WINDOW_S", defaults.qc.window_s)),
        )


class _Reader:
    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = env

    def _raw(self, name: str) -> str | None:
        raw = self.env.get(name, "").strip()
        return raw or None

    def text(self, name: str, default: str) -> str:
        return self._raw(name) or default

    def number(self, name: str, default: float) -> float:
        raw = self._raw(name)
        if raw is None:
            return default
        try:
            value = float(raw)
        except ValueError:
            raise SettingsError(f"{name}={raw!r}: expected a number") from None
        if not math.isfinite(value):
            raise SettingsError(f"{name}={raw!r}: expected a finite number")
        return value

    def positive(self, name: str, default: float) -> float:
        value = self.number(name, default)
        if value <= 0:
            raise SettingsError(f"{name}={value!r}: must be > 0")
        return value

    def integer(self, name: str, default: int | None, *, minimum: int | None = None) -> int | None:
        raw = self._raw(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            raise SettingsError(f"{name}={raw!r}: expected an integer") from None
        if minimum is not None and value < minimum:
            raise SettingsError(f"{name}={value}: must be >= {minimum}")
        return value

    def flag(self, name: str, default: bool) -> bool:
        raw = self._raw(name)
        if raw is None:
            return default
        if raw.lower() in ("1", "true", "yes", "on"):
            return True
        if raw.lower() in ("0", "false", "no", "off"):
            return False
        raise SettingsError(f"{name}={raw!r}: expected 1/0/true/false")
