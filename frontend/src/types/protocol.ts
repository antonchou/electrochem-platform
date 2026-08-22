/**
 * 与后端约定的实时数据协议（基准见《Web界面开发任务书》3.1 + SRS v0.2 数据分层）。
 * 本模块是协议的唯一事实来源：前端解析、校验、类型推导都从这里走。
 *
 * V2 帧（电极 I–V 链路）：
 *   Raw        voltage_raw_v / current_raw_a / temperature_raw_c
 *   Calibrated voltage_cal_v / current_cal_a / conductance_s / kappa_t_us_cm
 *   Derived    kappa_25_us_cm（未校准时为空）
 *   Configuration excitation_frequency_hz / excitation_amplitude_v / range_id /
 *              compensation_model / alpha_per_c
 *   Trace      seq_no / timestamp_utc / monotonic_ms / sensor_path_id / calibration_id
 *   Quality    quality_flags
 *
 * V1 废弃兼容别名（迁移期保留，不再代表原始数据）：
 *   ec = kappa_t_us_cm 的别名；temperature = temperature_raw_c 的别名；timestamp = 实验经过秒
 */

/** 实验状态 */
export type ExperimentStatus = 'idle' | 'running' | 'stopped' | 'error';
export type ExperimentHistoryStatus = 'running' | 'stopped' | 'aborted' | 'error';

/** 实时数据帧：归一化后的完整一帧（V2 字段 + V1 兼容别名，全可选） */
export interface ExperimentFrame {
  schema_version?: string;
  seq_no?: number;
  timestamp_utc?: string;
  monotonic_ms?: number;
  status: ExperimentStatus;
  // ---- Raw（不可变原始量） ----
  voltage_raw_v?: number;
  current_raw_a?: number;
  temperature_raw_c?: number;
  // ---- Calibrated ----
  voltage_cal_v?: number;
  current_cal_a?: number;
  conductance_s?: number;
  kappa_t_us_cm?: number;
  // ---- Derived（未校准时为空） ----
  kappa_25_us_cm?: number | null;
  // ---- Configuration ----
  excitation_frequency_hz?: number;
  excitation_amplitude_v?: number;
  range_id?: string;
  compensation_model?: string;
  alpha_per_c?: number;
  // ---- Trace ----
  sensor_path_id?: string;
  calibration_id?: string;
  // ---- Quality ----
  quality_flags?: string[];
  // ---- V1 废弃兼容别名（迁移期保留） ----
  ec?: number; // = kappa_t_us_cm 别名
  temperature?: number; // = temperature_raw_c 别名
  timestamp?: number; // 实验经过秒，前端曲线 X 轴
}

/** 纯状态帧（后端在某些时刻只下发状态，如 stopped） */
export interface StatusFrame {
  status: ExperimentStatus;
  timestamp?: number;
}

/** 服务端可能下发的所有消息 */
export type ServerMessage = ExperimentFrame | StatusFrame;

/** 前端下发的控制指令（REST API 通道） */
export type ControlAction = 'start' | 'stop' | 'reset';

/** 控制接口的响应 */
export interface ControlResponse {
  ok: boolean;
  status: ExperimentStatus;
  message?: string;
  /** Phase 7：当前实验在数据库中的 id（用于导出/历史） */
  experiment_id?: number;
  sample_id?: string;
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
}

/** 实验详情 */
export interface ExperimentDetail extends ExperimentSummary {
  samples: SampleSummary[];
  operator?: string | null;
  objective?: string | null;
  metadata_json?: string | null;
}

/** 原始帧记录（历史查询/导出用；旧库缺列时为 null） */
export interface RawFrame {
  id: number;
  sample_id: string | null;
  sensor_path_id: string;
  seq_no: number | null;
  timestamp_utc: string | null;
  monotonic_ms: number | null;
  t_seconds: number | null;
  schema_version?: string | null;
  legacy_ec_us_cm?: number | null; // V1 ec 废弃别名迁移列
  temperature_raw_c?: number | null;
  voltage_raw_v?: number | null;
  current_raw_a?: number | null;
  voltage_cal_v?: number | null;
  current_cal_a?: number | null;
  conductance_s?: number | null;
  kappa_t_us_cm?: number | null;
  kappa_25_us_cm?: number | null;
  k25?: number | null;
  excitation_frequency_hz?: number | null;
  excitation_amplitude_v?: number | null;
  range_id?: string | null;
  compensation_model?: string | null;
  alpha_per_c?: number | null;
  calibration_id?: string | null;
  quality_flags: string | null;
  status: string | null;
}

/** 开始实验的可选参数（含 I–V 校准/激励上下文） */
export interface ExperimentStartOptions {
  sample_id?: string;
  sensor_path_id?: string;
  title?: string;
  operator?: string;
  objective?: string;
  concentration_mmol_l?: number;
  calibration_id?: string;
  cell_constant_cm_inv?: number;
  alpha_per_c?: number;
  compensation_model?: string;
  calibration_valid_until_utc?: string;
  excitation_frequency_hz?: number;
  excitation_amplitude_v?: number;
  range_id?: string;
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

/**
 * 前端内部使用的曲线/统计数据点（数据分层明确）。
 * 主显示量：kt（κ(T)，当前温度电导率）；k25（温补后，未校准时为 null）。
 * 原始/诊断量：u（U）、i（I，mA）、g（G，S）。
 */
export interface DataPoint {
  /** 实验经过秒 */
  t: number;
  /** Calibrated：实测温度电导率 κ(T)，μS/cm */
  kt: number;
  /** Derived：温补后 κ25，μS/cm；未校准时为 null */
  k25: number | null;
  /** Raw：温度 T，°C */
  tc: number;
  /** Raw：电极电压 U，V（可空） */
  u: number | null;
  /** Raw：回路电流 I，mA（可空） */
  i: number | null;
  /** Calibrated：电导 G，S（可空） */
  g: number | null;
  /** Configuration：激励频率 Hz / 幅值 V / 量程 */
  freq?: number;
  amp?: number;
  rangeId?: string;
  /** Trace：链路 / 校准标识 */
  sensorPathId?: string;
  calibrationId?: string;
  /** Quality：质量标志 */
  qualityFlags?: string[];
  /** 浓度 mmol/L（可选）。实时帧/历史帧暂无该字段；浓度轴拟合时缺省用序号 1..N 占位 */
  concentration?: number;
}

/** 客户端事件总线（供 hooks 订阅） */
export type ClientEvent =
  | { type: 'message'; frame: ExperimentFrame }
  | { type: 'status'; status: ExperimentStatus }
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
 * - V2 帧：含测量量（voltage_raw_v / kappa_t_us_cm 等）；只含 status → 状态帧。
 * - V1 帧：timestamp+ec+temperature → 归一化为 V2 字段（兼容迁移期）。
 * - 坏数据（非法数值 / 未知状态 / 缺测量字段）→ 返回 null，页面不崩溃（F10）。
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

  // V2 测量标志：出现任一 Raw/Calibrated/Derived 字段即视为测量帧
  const v2MeasureKeys = ['voltage_raw_v', 'current_raw_a', 'temperature_raw_c', 'kappa_t_us_cm'] as const;
  const hasV2Measure = v2MeasureKeys.some((key) => raw[key] !== undefined);

  if (hasV2Measure) {
    return parseV2Frame(raw, status as ExperimentStatus);
  }

  // V1 兼容：timestamp + ec + temperature 三字段必须齐全
  if (raw.timestamp !== undefined || raw.ec !== undefined || raw.temperature !== undefined) {
    const timestamp = parseFiniteNumber(raw.timestamp);
    const ec = parseFiniteNumber(raw.ec);
    const temperature = parseFiniteNumber(raw.temperature);
    if (timestamp === null || ec === null || temperature === null) return null;
    // 归一化为 V2 字段：ec = κ(T) 别名；temperature = T 别名
    const frame: ExperimentFrame = {
      timestamp,
      status: status as ExperimentStatus,
      ec,
      temperature,
      kappa_t_us_cm: ec,
      temperature_raw_c: temperature,
      schema_version: '1.0',
    };
    attachOptional(frame, raw);
    return frame;
  }

  // 纯状态帧
  return { status: status as ExperimentStatus } as StatusFrame;
}

function parseV2Frame(raw: Record<string, unknown>, status: ExperimentStatus): ExperimentFrame | null {
  // 原始量缺测时整帧视为不完整（bad frame），返回 null 由调用方提示
  const timestamp = parseFiniteNumber(raw.timestamp);
  const temperatureRawC = parseFiniteNumber(raw.temperature_raw_c);
  const voltageRawV = parseFiniteNumber(raw.voltage_raw_v);
  const currentRawA = parseFiniteNumber(raw.current_raw_a);

  const frame: ExperimentFrame = { status };
  if (timestamp !== null) frame.timestamp = timestamp;
  if (temperatureRawC !== null) {
    frame.temperature_raw_c = temperatureRawC;
    frame.temperature = temperatureRawC; // V1 废弃别名
  }
  if (voltageRawV !== null) frame.voltage_raw_v = voltageRawV;
  if (currentRawA !== null) frame.current_raw_a = currentRawA;

  if (typeof raw.schema_version === 'string') frame.schema_version = raw.schema_version;
  if (typeof raw.seq_no === 'number' && Number.isFinite(raw.seq_no)) frame.seq_no = raw.seq_no;
  if (typeof raw.timestamp_utc === 'string') frame.timestamp_utc = raw.timestamp_utc;
  if (typeof raw.monotonic_ms === 'number' && Number.isFinite(raw.monotonic_ms)) {
    frame.monotonic_ms = raw.monotonic_ms;
  }

  attachOptional(frame, raw);
  return frame;
}

/** 透传可选数值/字符串/数组字段（存在且合法才写入；坏值忽略，不使整帧失效） */
function attachOptional(frame: ExperimentFrame, raw: Record<string, unknown>): void {
  const optionalNumberFields = [
    'voltage_cal_v',
    'current_cal_a',
    'conductance_s',
    'kappa_t_us_cm',
    'kappa_25_us_cm',
    'excitation_frequency_hz',
    'excitation_amplitude_v',
    'alpha_per_c',
  ] as const;
  for (const key of optionalNumberFields) {
    if (raw[key] === undefined) continue;
    const parsed = parseFiniteNumber(raw[key]);
    if (parsed !== null) frame[key] = parsed;
  }
  const optionalStringFields = [
    'range_id',
    'compensation_model',
    'sensor_path_id',
    'calibration_id',
  ] as const;
  for (const key of optionalStringFields) {
    if (typeof raw[key] === 'string' && (raw[key] as string).length > 0) frame[key] = raw[key];
  }
  if (Array.isArray(raw.quality_flags)) {
    const flags = raw.quality_flags.filter((f): f is string => typeof f === 'string');
    if (flags.length > 0) frame.quality_flags = flags;
  }
  // V1 废弃别名补充（部分老实现仅发 ec/temperature）
  if (frame.kappa_t_us_cm !== undefined && frame.ec === undefined) {
    frame.ec = frame.kappa_t_us_cm;
  }
  if (frame.temperature_raw_c !== undefined && frame.temperature === undefined) {
    frame.temperature = frame.temperature_raw_c;
  }
}
