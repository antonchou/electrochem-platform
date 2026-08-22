"""实验状态单例。模拟数据源与真实后端解耦：真实后端接入后本模块被替换，前端协议不变。"""

import asyncio
import time
from typing import Optional

from .schemas import ExperimentStatus


class ExperimentState:
    """实验运行状态（内存态）+ 当前实验持久化上下文。"""

    def __init__(self) -> None:
        self.status: ExperimentStatus = "idle"
        self.t0: Optional[float] = None
        self.lock = asyncio.Lock()
        # Phase 7：当前实验持久化上下文
        self.experiment_db_id: Optional[int] = None
        self.experiment_uid: Optional[str] = None
        self.sample_id: str = "SAMPLE"
        self.sensor_path_id: str = "CM2_WIDE"
        self.seq_no: int = 0
        # 电极 I–V 链路校准/激励上下文（SRS v0.2 §3 / §5，随实验版本化）
        self.calibration_id: Optional[str] = None
        self.cell_constant_cm_inv: Optional[float] = None  # Kcell
        self.calibration_valid_until_utc: Optional[str] = None
        self.alpha_per_c: Optional[float] = None
        self.compensation_model: Optional[str] = None
        self.excitation_frequency_hz: Optional[float] = None
        self.excitation_amplitude_v: Optional[float] = None
        self.range_id: Optional[str] = None

    async def start(
        self,
        *,
        sample_id: str = "SAMPLE",
        sensor_path_id: str = "CM2_WIDE",
        title: str = "不同溶液导电性相对比较",
        experiment_db_id: Optional[int] = None,
        experiment_uid: Optional[str] = None,
        calibration_id: Optional[str] = None,
        cell_constant_cm_inv: Optional[float] = None,
        calibration_valid_until_utc: Optional[str] = None,
        alpha_per_c: Optional[float] = None,
        compensation_model: Optional[str] = None,
        excitation_frequency_hz: Optional[float] = None,
        excitation_amplitude_v: Optional[float] = None,
        range_id: Optional[str] = None,
    ) -> bool:
        """成功进入 running 返回 True；已在运行返回 False。

        进入 running 的同时原子绑定新实验的持久化上下文（experiment_db_id 等），
        确保采集任务一旦看到 running，落库目标就是新实验记录（P1-3 修复）。
        """
        async with self.lock:
            if self.status == "running":
                return False
            self.status = "running"
            self.t0 = time.monotonic()
            self.seq_no = 0
            self.sample_id = sample_id
            self.sensor_path_id = sensor_path_id
            self.experiment_db_id = experiment_db_id
            self.experiment_uid = experiment_uid
            self.calibration_id = calibration_id
            self.cell_constant_cm_inv = cell_constant_cm_inv
            self.calibration_valid_until_utc = calibration_valid_until_utc
            self.alpha_per_c = alpha_per_c
            self.compensation_model = compensation_model
            self.excitation_frequency_hz = excitation_frequency_hz
            self.excitation_amplitude_v = excitation_amplitude_v
            self.range_id = range_id
            return True

    async def stop(self) -> bool:
        async with self.lock:
            changed = self.status == "running"
            if changed:
                self.status = "stopped"
            return changed

    async def reset(self) -> None:
        async with self.lock:
            self.status = "idle"
            self.t0 = None
            self.experiment_db_id = None
            self.experiment_uid = None
            self.calibration_id = None
            self.cell_constant_cm_inv = None
            self.calibration_valid_until_utc = None
            self.alpha_per_c = None
            self.compensation_model = None
            self.excitation_frequency_hz = None
            self.excitation_amplitude_v = None
            self.range_id = None

    def next_seq(self) -> int:
        self.seq_no += 1
        return self.seq_no

    def elapsed(self) -> float:
        """实验已运行秒数（running 状态才有意义）。"""
        if self.t0 is None:
            return 0.0
        return time.monotonic() - self.t0


state = ExperimentState()
