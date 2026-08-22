"""与前端约定的数据模型（协议基准见《Web界面开发任务书》3.1）。"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

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

    在线 V2 不包含 V1 `ec`/`temperature`/`timestamp` 别名；旧记录兼容由存储层负责。
    """

    message_type: Literal["measurement"]
    schema_version: Literal["2.0"]
    experiment_uid: str
    concentration_mmol_l: Optional[float] = None
    seq_no: int = Field(ge=1)
    timestamp_utc: str
    monotonic_ms: int = Field(ge=0)
    t_seconds: float = Field(ge=0.0)
    status: Literal["running"]
    # ---- Raw（不可变原始量） ----
    voltage_raw_v: Optional[float]  # 电极电压 U，V；缺测质量帧为 null
    current_raw_a: Optional[float]  # 回路电流 I，A；缺测质量帧为 null
    temperature_raw_c: Optional[float]  # 温度 T，°C；缺测质量帧为 null
    # ---- Calibrated ----
    voltage_cal_v: Optional[float]  # 电压通道校准后，V
    current_cal_a: Optional[float]  # 电流通道校准后，A
    conductance_s: Optional[float]  # G(T)=I/U，S
    kappa_t_us_cm: Optional[float]  # κ(T)=Kcell·G，μS/cm
    # ---- Derived ----
    kappa_25_us_cm: Optional[float]  # 温补后 κ25，μS/cm
    # ---- Configuration（随实验版本化） ----
    excitation_frequency_hz: Optional[float]
    excitation_amplitude_v: Optional[float]
    range_id: Optional[str]
    sensor_path_id: Optional[str]
    compensation_model: Optional[str]
    alpha_per_c: Optional[float]
    # ---- Trace ----
    calibration_id: Optional[str]
    cell_constant_cm_inv: Optional[float]
    calibration_valid_until_utc: Optional[str]
    # ---- Quality ----
    quality_flags: list[str]


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
    concentration_mmol_l: Optional[float] = Field(default=None, ge=0.0)
    # ---- 电极 I–V 链路校准/激励（可选，缺省用设备/配置默认） ----
    calibration_id: Optional[str] = None
    cell_constant_cm_inv: Optional[float] = Field(default=None, gt=0.0)  # Kcell，cm⁻¹
    # 保证 10–40°C 有效温区内线性补偿分母始终为正；典型水溶液约 0.02/°C。
    alpha_per_c: Optional[float] = Field(default=None, ge=0.0, lt=1.0 / 15.0)
    compensation_model: Optional[Literal["linear", "none"]] = None
    calibration_valid_until_utc: Optional[str] = None  # 校准有效期
    excitation_frequency_hz: Optional[float] = Field(default=None, gt=0.0)  # 激励频率，Hz
    excitation_amplitude_v: Optional[float] = Field(default=None, gt=0.0)  # 激励幅值，V
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
