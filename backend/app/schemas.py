"""与前端约定的数据模型（协议基准见《Web界面开发任务书》3.1）。"""

from typing import Literal, Optional

from pydantic import BaseModel

ExperimentStatus = Literal["idle", "running", "stopped", "error"]


class Frame(BaseModel):
    """实时数据帧。"""

    timestamp: float  # 实验开始后的时间，单位秒
    ec: float  # 电导率，μS/cm
    temperature: float  # 温度，°C
    status: ExperimentStatus


class ControlResponse(BaseModel):
    """控制接口响应。"""

    ok: bool
    status: ExperimentStatus
    message: Optional[str] = None
    experiment_id: Optional[int] = None
    sample_id: Optional[str] = None


class ExperimentStartRequest(BaseModel):
    """开始实验的可选参数（用于样品溯源，Phase 7）。"""

    sample_id: Optional[str] = None
    sensor_path_id: Optional[str] = None
    title: Optional[str] = None
    operator: Optional[str] = None
    objective: Optional[str] = None
    concentration_mmol_l: Optional[float] = None


class FitRequest(BaseModel):
    """备选公式拟合请求。

    x_axis 标明 X 轴物理含义，决定可用模型池（化学语境）：
    - time：时间序列（线性/二次/一阶指数饱和/指数/对数/幂）
    - temperature：温度 °C（线性温补/二次/Arrhenius）
    - concentration：浓度（线性标定/二次/Kohlrausch）
    """

    x: list[float]
    y: list[float]
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
