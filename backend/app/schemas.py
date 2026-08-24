"""与前端约定的数据模型（协议基准见《Web界面开发任务书》3.1）。"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

ExperimentStatus = Literal["idle", "running", "stopped", "error"]


class Frame(BaseModel):
    """实时数据帧。

    V1 兼容字段：timestamp / ec / temperature / status（旧前端不变）。
    I–V 链路扩展（REQ-M-001 + SRS 3.3 软件侧）：电压/电流/温度原始量，
    G/κ(T)/κ25 可重算结果，以及协议溯源字段。旧字段 ec 仍是 κ25 的别名。
    """

    timestamp: float  # 实验开始后的时间，单位秒
    ec: Optional[float] = None  # 电导率，μS/cm（兼容层 = κ25；COMPUTE_INVALID 时为 null）
    temperature: float  # 温度，°C（兼容层 = temperature_raw_c）
    status: ExperimentStatus
    # I–V 链路（软件侧，真实硬件未接时由 Mock 仿真）
    schema_version: Optional[int] = 2
    device_id: Optional[str] = None
    firmware_version: Optional[str] = None
    range_id: Optional[str] = None
    voltage_raw_v: Optional[float] = None
    current_raw_a: Optional[float] = None
    temperature_raw_c: Optional[float] = None
    conductance_s: Optional[float] = None
    kappa_t_us_cm: Optional[float] = None
    kappa_25_us_cm: Optional[float] = None
    quality_flags: Optional[str] = None


class ControlResponse(BaseModel):
    """控制接口响应。"""

    ok: bool
    status: ExperimentStatus
    message: Optional[str] = None
    experiment_id: Optional[int] = None
    sample_id: Optional[str] = None
    resumed: Optional[bool] = None


class ExperimentStartRequest(BaseModel):
    """开始实验的可选参数（用于样品溯源，Phase 7）。"""

    sample_id: Optional[str] = None
    sensor_path_id: Optional[str] = None
    title: Optional[str] = None
    operator: Optional[str] = None
    objective: Optional[str] = None
    concentration_mmol_l: Optional[float] = Field(default=None, ge=0)


class FitRequest(BaseModel):
    """备选公式拟合请求。

    x_axis 标明 X 轴物理含义，决定可用模型池（化学语境）：
    - time：时间序列（线性/二次/一阶指数饱和/指数/对数/幂）
    - temperature：温度 °C（线性温补/二次/Arrhenius）
    - concentration：浓度（线性标定/二次/Kohlrausch）
    """

    x: list[float] = Field(..., min_length=3, max_length=20_000)
    y: list[float] = Field(..., min_length=3, max_length=20_000)
    models: Optional[list[str]] = None  # 缺省 = 该轴全部模型
    x_axis: Literal["time", "temperature", "concentration"] = "time"


class FitResultItem(BaseModel):
    """单模型拟合结果。"""

    model: str
    label: str
    params: dict
    r2: float
    rmse: float
    n: int
    fitted: list[list[float]]  # 拟合曲线采样点 [[x, y], ...]


class FitResponse(BaseModel):
    best: Optional[str] = None
    models: list[FitResultItem]


class CurrentExperimentResponse(BaseModel):
    """当前内存态实验（供前端重连后恢复 experiment_id / 样品号）。"""

    status: ExperimentStatus
    experiment_id: Optional[int] = None
    sample_id: Optional[str] = None
    experiment_uid: Optional[str] = None
