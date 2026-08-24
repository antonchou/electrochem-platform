/**
 * 与后端约定的实时数据协议（基准见《Web界面开发任务书》3.1）。
 * 本模块是协议的唯一事实来源：前端解析、校验、类型推导都从这里走。
 */

/** 实验状态 */
export type ExperimentStatus = 'idle' | 'running' | 'stopped' | 'error';
export type ExperimentHistoryStatus = 'running' | 'stopped' | 'aborted' | 'error';

/** 实时数据帧：数据到达后的完整一帧 */
export interface ExperimentFrame {
  /** 实验开始后的时间，单位秒 */
  timestamp: number;
  /** 电导率，单位 μS/cm（兼容层 = κ25；COMPUTE_INVALID 时为 null） */
  ec: number | null;
  /** 温度，单位 °C */
  temperature: number;
  /** 状态字段 */
  status: ExperimentStatus;
  // ---- I–V 测量链路扩展（REQ-M-001/REQ-U-001，后端 v2+ 才下发，均可选） ----
  /** 协议版本 */
  schema_version?: number;
  device_id?: string;
  firmware_version?: string;
  range_id?: string;
  /** 原始电压 V（不可变原始量） */
  voltage_raw_v?: number;
  /** 原始电流 A（不可变原始量） */
  current_raw_a?: number;
  /** 原始温度 °C（不可变原始量） */
  temperature_raw_c?: number;
  /** 电导 G = I/U，S */
  conductance_s?: number;
  /** 电导率 κ(T)，μS/cm */
  kappa_t_us_cm?: number;
  /** 温补后电导率 κ25，μS/cm */
  kappa_25_us_cm?: number;
  /** 质量标志（| 分隔） */
  quality_flags?: string | null;
}

/** 纯状态帧（后端在某些时刻只下发状态，如 stopped） */
export interface StatusFrame {
  status: ExperimentStatus;
  timestamp?: number;
  experiment_id?: number;
}

/** 服务端可能下发的所有消息 */
export type ServerMessage = ExperimentFrame | StatusFrame;

/** 前端下发的控制指令（REST API 通道） */
export type ControlAction = 'start' | 'stop' | 'reset';

/** 当前实验（重连恢复） */
export interface CurrentExperiment {
  status: ExperimentStatus;
  experiment_id?: number | null;
  sample_id?: string | null;
  experiment_uid?: string | null;
}

/** 控制接口的响应 */
export interface ControlResponse {
  ok: boolean;
  status: ExperimentStatus;
  message?: string;
  /** Phase 7：当前实验在数据库中的 id（用于导出/历史） */
  experiment_id?: number;
  sample_id?: string;
  resumed?: boolean;
}

/** 历史实验摘要（列表项） */
export interface ExperimentSummary {
  id: number;
  experiment_id: string;
  title: string;
  status: ExperimentHistoryStatus;
  sample_id: string | null;
  sensor_path_id: string | null;
  started_at_utc: string;
  ended_at_utc: string | null;
  frame_count: number;
}

/** 样品汇总 */
export interface SampleSummary {
  id: number;
  sample_id: string;
  sensor_path_id: string | null;
  concentration_mmol_l: number | null;
  composition: string | null;
  measured_at_utc: string | null;
  k25_median: number | null;
  k25_mean: number | null;
  k25_sd: number | null;
  frame_count: number;
  // 判稳与 QC（REQ-D-003，后端 v3+）：实验停止时自动判稳写入
  qc_status?: string | null;
  qc_reason?: string | null;
  representative_value?: number | null;
  qc_checked_at_utc?: string | null;
}

/** 实验详情 */
export interface ExperimentDetail extends ExperimentSummary {
  samples: SampleSummary[];
  operator?: string | null;
  objective?: string | null;
  metadata_json?: string | null;
}

/** 原始帧记录（历史查询/导出用） */
export interface RawFrame {
  id: number;
  sample_id: string | null;
  sensor_path_id: string;
  seq_no: number | null;
  timestamp_utc: string | null;
  monotonic_ms: number | null;
  t_seconds: number | null;
  ec_raw: number | null;
  temperature_raw: number;
  k25: number | null;
  quality_flags: string | null;
  status: string | null;
  // I–V 计算链列（后端 v2+）
  voltage_raw_v?: number | null;
  current_raw_a?: number | null;
  conductance_s?: number | null;
  kappa_t_us_cm?: number | null;
  kappa_25_us_cm?: number | null;
}

/** 开始实验的可选参数 */
export interface ExperimentStartOptions {
  sample_id?: string;
  sensor_path_id?: string;
  title?: string;
}

/** 拟合 X 轴物理含义（决定化学模型池） */
export type FitAxis = 'time' | 'temperature' | 'concentration';

/** 单模型拟合结果 */
export interface FitResultItem {
  model: string;
  label: string;
  params: Record<string, number>;
  r2: number;
  rmse: number;
  n: number;
  /** 拟合曲线采样点 [x, y] */
  fitted: [number, number][];
}

/** 拟合接口响应 */
export interface FitResponse {
  best: string | null;
  models: FitResultItem[];
}

/** WebSocket 连接状态 */
export type ConnectionStatus =
  | 'idle' // 未连接
  | 'connecting' // 连接中
  | 'connected' // 已连接
  | 'reconnecting' // 断线重连中
  | 'disconnected'; // 已断开

/** 前端内部使用的曲线数据点 */
export interface DataPoint {
  /** 秒 */
  t: number;
  /** μS/cm；计算链拒绝时为 null */
  ec: number | null;
  /** °C */
  tc: number;
  /** 浓度 mmol/L（可选）。实时帧/历史帧暂无该字段；浓度轴拟合时缺省用序号 1..N 占位 */
  concentration?: number;
  // ---- I–V 测量链路（REQ-U-001 分层显示，可选） ----
  /** 原始电压 V */
  voltage_raw_v?: number;
  /** 原始电流 A */
  current_raw_a?: number;
  /** 电导 G = I/U，S */
  conductance_s?: number;
  /** 电导率 κ(T)，μS/cm */
  kappa_t_us_cm?: number;
  /** 温补后电导率 κ25，μS/cm */
  kappa_25_us_cm?: number;
  /** 质量标志 */
  quality_flags?: string;
}

/** 客户端事件总线（供 hooks 订阅） */
export type ClientEvent =
  | { type: 'message'; frame: ExperimentFrame }
  | { type: 'status'; status: ExperimentStatus; experiment_id?: number }
  | { type: 'connection'; status: ConnectionStatus }
  | { type: 'error'; message: string };

const VALID_STATUS: readonly ExperimentStatus[] = ['idle', 'running', 'stopped', 'error'];

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null;
}

function parseFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string' || value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * 解析并校验一条服务端消息。
 * 字段缺失 / 非法数值 / 未知状态 → 返回 null（调用方丢弃并提示，页面不崩溃）。
 * 数值允许字符串形式（如 "1412.8"），统一 Number() 归一。
 */
export function parseServerMessage(raw: unknown): ServerMessage | null {
  if (typeof raw === 'string') {
    try {
      raw = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!isObject(raw)) return null;

  const status = raw.status;
  if (typeof status !== 'string' || !VALID_STATUS.includes(status as ExperimentStatus)) {
    return null;
  }

  const hasTimestamp = raw.timestamp !== undefined;
  const hasTemperature = raw.temperature !== undefined;
  const hasEc = raw.ec !== undefined;

  if (!hasTimestamp && !hasEc && !hasTemperature) {
    const frame: StatusFrame = { status: status as ExperimentStatus };
    if (typeof raw.experiment_id === 'number') frame.experiment_id = raw.experiment_id;
    return frame;
  }
  if (!hasTimestamp || !hasTemperature) return null;

  const timestamp = parseFiniteNumber(raw.timestamp);
  const temperature = parseFiniteNumber(raw.temperature);
  if (timestamp === null || temperature === null) return null;

  // COMPUTE_INVALID / 缺 κ25：ec 允许为 null；非空则必须是有限数字。
  let ec: number | null = null;
  if (raw.ec !== null && raw.ec !== undefined) {
    ec = parseFiniteNumber(raw.ec);
    if (ec === null) return null;
  }

  // I–V 测量链路扩展字段（REQ-M-001/REQ-U-001）：全部可选，缺字段也接受
  // （兼容旧后端/旧浏览器模拟）。后端 v2+ 才下发，前端拿不到时显示为空。
  return {
    timestamp,
    ec,
    temperature,
    status: status as ExperimentStatus,
    schema_version: typeof raw.schema_version === 'number' ? raw.schema_version : undefined,
    device_id: typeof raw.device_id === 'string' ? raw.device_id : undefined,
    firmware_version: typeof raw.firmware_version === 'string' ? raw.firmware_version : undefined,
    range_id: typeof raw.range_id === 'string' ? raw.range_id : undefined,
    voltage_raw_v: parseFiniteNumber(raw.voltage_raw_v) ?? undefined,
    current_raw_a: parseFiniteNumber(raw.current_raw_a) ?? undefined,
    temperature_raw_c: parseFiniteNumber(raw.temperature_raw_c) ?? undefined,
    conductance_s: parseFiniteNumber(raw.conductance_s) ?? undefined,
    kappa_t_us_cm: parseFiniteNumber(raw.kappa_t_us_cm) ?? undefined,
    kappa_25_us_cm: parseFiniteNumber(raw.kappa_25_us_cm) ?? undefined,
    quality_flags:
      typeof raw.quality_flags === 'string' && raw.quality_flags.length > 0
        ? raw.quality_flags
        : undefined,
  };
}
