"""实验状态单例。模拟数据源与真实后端解耦：真实后端接入后本模块被替换，前端协议不变。"""

import asyncio
import threading
import time
from typing import Optional

from .schemas import ExperimentStatus

DEFAULT_SAMPLE_ID = "SAMPLE"
DEFAULT_SENSOR_PATH_ID = "MOCK_EC_IV"


class ExperimentState:
    """实验运行状态（内存态）+ 当前实验持久化上下文。"""

    def __init__(self) -> None:
        self.status: ExperimentStatus = "idle"
        self.t0: Optional[float] = None
        self.lock = asyncio.Lock()
        # Phase 7：当前实验持久化上下文
        self.experiment_db_id: Optional[int] = None
        self.experiment_uid: Optional[str] = None
        self.sample_id: str = DEFAULT_SAMPLE_ID
        self.sensor_path_id: str = DEFAULT_SENSOR_PATH_ID
        self.calibration_id: Optional[str] = None
        self.seq_no: int = 0
        self._seq_lock = threading.Lock()
        self._paused_at: Optional[float] = None

    async def start(
        self,
        *,
        sample_id: str = DEFAULT_SAMPLE_ID,
        sensor_path_id: str = DEFAULT_SENSOR_PATH_ID,
        title: str = "不同溶液导电性相对比较",
        experiment_db_id: Optional[int] = None,
        experiment_uid: Optional[str] = None,
        calibration_id: Optional[str] = None,
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
            self._paused_at = None
            self.seq_no = 0
            self.sample_id = sample_id
            self.sensor_path_id = sensor_path_id
            self.experiment_db_id = experiment_db_id
            self.experiment_uid = experiment_uid
            self.calibration_id = calibration_id
            return True

    async def stop(self) -> bool:
        async with self.lock:
            changed = self.status == "running"
            if changed:
                self.status = "stopped"
                self._paused_at = time.monotonic()
            return changed

    async def resume(self) -> bool:
        """stopped → running，保留 seq_no / 实验上下文；扣除暂停墙钟，elapsed 从停点续上。"""
        async with self.lock:
            if self.status != "stopped" or self.experiment_db_id is None:
                return False
            now = time.monotonic()
            if self.t0 is None:
                self.t0 = now
            elif self._paused_at is not None:
                self.t0 += now - self._paused_at
            self._paused_at = None
            self.status = "running"
            return True

    async def reset(self) -> None:
        async with self.lock:
            self.status = "idle"
            self.t0 = None
            self._paused_at = None
            self.experiment_db_id = None
            self.experiment_uid = None
            self.calibration_id = None

    def next_seq(self) -> int:
        """Monotonic seq for the current experiment. Safe if a second task appears."""
        with self._seq_lock:
            self.seq_no += 1
            return self.seq_no

    def elapsed(self) -> float:
        """实验已运行秒数（不含暂停墙钟）。"""
        if self.t0 is None:
            return 0.0
        end = self._paused_at if self.status != "running" and self._paused_at is not None else time.monotonic()
        return max(0.0, end - self.t0)


state = ExperimentState()
