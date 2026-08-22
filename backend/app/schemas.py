"""与前端约定的数据模型（协议基准见《Web界面开发任务书》3.1）。"""

from typing import Literal, Optional

from pydantic import BaseModel

ExperimentStatus = Literal["idle", "running", "stopped", "error"]


class Frame(BaseModel):
    """实时数据帧 V2（电极 I–V 链路，SRS v0.2 数据分层）。

    Raw（不可变）：voltage_raw_v / current_raw_a / temperature_raw_c
    Calibrated：voltage_cal_v / current_cal_a / conductance_s / kappa_t_us_cm
    Derived：kappa_25_us_cm
    Trace：seq_no / timestamp_utc / monotonic_ms / sensor_path_id / calibration_id
    Configuration：excitation_frequency_hz / excitation_amplitude_v / range_id /
                   compensation_model / alpha_per_c（随实验版本化）
    Quality：quality_flags

    迁移期废弃兼容别名（不代表正式原始数据）：
        ec          = kappa_t_us_cm 的别名
        temperature = temperature_raw_c 的别名
        timestamp   = 实验经过秒（前端曲线 X 轴）
    """

    schema_version: str = "2.0"
    seq_no: Optional[int] = None
    timestamp_utc: Optional[str] = None
    monotonic_ms: Optional[int] = None
    status: ExperimentStatus
    # ---- Raw（不可变原始量） ----
    voltage_raw_v: Optional[float] = None  # 电极电压 U，V
    current_raw_a: Optional[float] = None  # 回路电流 I，A
    temperature_raw_c: Optional[float] = None  # 温度 T，°C
    # ---- Calibrated ----
    voltage_cal_v: Optional[float] = None  # 电压通道校准后，V
    current_cal_a: Optional[float] = None  # 电流通道校准后，A
    conductance_s: Optional[float] = None  # G(T)=I/U，S
    kappa_t_us_cm: Optional[float] = None  # κ(T)=Kcell·G，μS/cm
    # ---- Derived ----
    kappa_25_us_cm: Optional[float] = None  # 温补后 κ25，μS/cm
    # ---- Configuration（随实验版本化） ----
    excitation_frequency_hz: Optional[float] = None
    excitation_amplitude_v: Optional[float] = None
    range_id: Optional[str] = None
    sensor_path_id: Optional[str] = None
    compensation_model: Optional[str] = None
    alpha_per_c: Optional[float] = None
    # ---- Trace ----
    calibration_id: Optional[str] = None
    # ---- Quality ----
    quality_flags: Optional[list[str]] = None
    # ---- V1 废弃兼容别名（迁移期保留） ----
    ec: Optional[float] = None  # = kappa_t_us_cm 的别名
    temperature: Optional[float] = None  # = temperature_raw_c 的别名
    timestamp: Optional[float] = None  # 实验经过秒，前端曲线 X 轴


class ControlResponse(BaseModel):
    """控制接口响应。"""

    ok: bool
    status: ExperimentStatus
    message: Optional[str] = None
    experiment_id: Optional[int] = None
    sample_id: Optional[str] = None


class ExperimentStartRequest(BaseModel):
    """开始实验的可选参数（样品溯源 + I–V 校准/激励上下文，SRS v0.2）。"""

    sample_id: Optional[str] = None
    sensor_path_id: Optional[str] = None
    title: Optional[str] = None
    operator: Optional[str] = None
    objective: Optional[str] = None
    concentration_mmol_l: Optional[float] = None
    # ---- 电极 I–V 链路校准/激励（可选，缺省用设备/配置默认） ----
    calibration_id: Optional[str] = None
    cell_constant_cm_inv: Optional[float] = None  # Kcell，cm⁻¹
    alpha_per_c: Optional[float] = None  # 线性温补系数 α，1/°C
    compensation_model: Optional[str] = None  # linear / none
    calibration_valid_until_utc: Optional[str] = None  # 校准有效期
    excitation_frequency_hz: Optional[float] = None  # 激励频率，Hz
    excitation_amplitude_v: Optional[float] = None  # 激励幅值，V
    range_id: Optional[str] = None  # 电流量程标识，如 R_100R_10K


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
