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
 * 在线通道严格使用 V2；V1 字段只允许出现在历史数据库读取模型 RawFrame 中。
 */

/** 实验状态 */
export type ExperimentStatus = 'idle' | 'running' | 'stopped' | 'error';
export type ExperimentHistoryStatus = 'running' | 'stopped' | 'aborted' | 'error';

/** 实时数据帧：键集合固定；缺测或不可计算量显式为 null，并由质量标志解释。 */
export interface ExperimentFrame {
  message_type: 'measurement';
  schema_version: '2.0';
  experiment_uid: string;
  concentration_mmol_l?: number;
  seq_no: number;
  timestamp_utc: string;
  monotonic_ms: number;
  t_seconds: number;
  status: 'running';
  // ---- Raw（不可变原始量） ----
  voltage_raw_v: number | null;
  current_raw_a: number | null;
  temperature_raw_c: number | null;
  // ---- Calibrated ----
  voltage_cal_v: number | null;
  current_cal_a: number | null;
  conductance_s: number | null;
  kappa_t_us_cm: number | null;
  // ---- Derived（未校准时为空） ----
  kappa_25_us_cm: number | null;
  // ---- Configuration ----
  excitation_frequency_hz: number | null;
  excitation_amplitude_v: number | null;
  range_id: string | null;
  compensation_model: string | null;
  alpha_per_c: number | null;
  // ---- Trace ----
  sensor_path_id: string | null;
  calibration_id: string | null;
  cell_constant_cm_inv: number | null;
  calibration_valid_until_utc: string | null;
  // ---- Quality ----
  quality_flags: string[];
}

/** 调试负载帧有独立溯源，只用于压测视图，不代表真实实验状态切换。 */
export function isDebugBurstFrame(frame: ExperimentFrame): boolean {
  return (
    frame.experiment_uid.startsWith('DEBUG-BURST-') &&
    frame.quality_flags.includes('DEBUG_BURST')
  );
}

/** 纯状态帧（后端在某些时刻只下发状态，如 stopped） */
export interface StatusFrame {
  message_type: 'status';
  status: ExperimentStatus;
  experiment_uid?: string;
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
  cell_constant_cm_inv?: number | null;
  calibration_valid_until_utc?: string | null;
  quality_flags: string | null;
  /** 历史 API 提供的结构化质量标志；quality_flags 文本列仅为存储/CSV 兼容。 */
  quality_flags_list?: string[];
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
  cell_constant_cm_inv?: number | null;
  alpha_per_c?: number | null;
  compensation_model?: string;
  calibration_valid_until_utc?: string;
  excitation_frequency_hz?: number;
  excitation_amplitude_v?: number | null;
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
  kt: number | null;
  /** Derived：温补后 κ25，μS/cm；未校准时为 null */
  k25: number | null;
  /** Raw：温度 T，°C */
  tc: number | null;
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
  cellConstant?: number | null;
  calibrationValidUntil?: string | null;
  compensationModel?: string;
  alphaPerC?: number | null;
  /** Quality：质量标志 */
  qualityFlags?: string[];
  /** 浓度 mmol/L（可选）；浓度拟合只接受真实值，不用样本序号占位。 */
  concentration?: number | null;
}

/** 客户端事件总线（供 hooks 订阅） */
export type ClientEvent =
  | { type: 'message'; frame: ExperimentFrame }
  | { type: 'status'; status: ExperimentStatus; experimentUid?: string }
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

function hasOwn(raw: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(raw, key);
}

function parseNullableNumber(raw: Record<string, unknown>, key: string): number | null | undefined {
  if (!hasOwn(raw, key)) return undefined;
  if (raw[key] === null) return null;
  return parseFiniteNumber(raw[key]) ?? undefined;
}

function parseNullableString(raw: Record<string, unknown>, key: string): string | null | undefined {
  if (!hasOwn(raw, key)) return undefined;
  if (raw[key] === null) return null;
  return typeof raw[key] === 'string' && raw[key].length > 0 ? raw[key] : undefined;
}

/**
 * 解析并校验一条服务端消息。
 * 在线协议不做版本猜测：只有显式 `message_type` 的严格 V2 帧才可进入实时缓冲。
 * V1 `ec/temperature/timestamp` 即使数值合法也返回 null，防止静默降级。
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
  if (raw.message_type === 'measurement') {
    return parseV2Frame(raw, status as ExperimentStatus);
  }
  if (raw.message_type !== 'status') return null;

  const statusFrame: StatusFrame = {
    message_type: 'status',
    status: status as ExperimentStatus,
  };
  if (typeof raw.experiment_uid === 'string' && raw.experiment_uid.length > 0) {
    statusFrame.experiment_uid = raw.experiment_uid;
  }
  return statusFrame;
}

function parseV2Frame(raw: Record<string, unknown>, status: ExperimentStatus): ExperimentFrame | null {
  if (status !== 'running' || raw.schema_version !== '2.0') return null;
  const experimentUid = raw.experiment_uid;
  const timestampUtc = raw.timestamp_utc;
  const seqNo = parseFiniteNumber(raw.seq_no);
  const monotonicMs = parseFiniteNumber(raw.monotonic_ms);
  const tSeconds = parseFiniteNumber(raw.t_seconds);
  if (
    typeof experimentUid !== 'string' ||
    experimentUid.length === 0 ||
    typeof timestampUtc !== 'string' ||
    timestampUtc.length === 0 ||
    seqNo === null ||
    !Number.isInteger(seqNo) ||
    seqNo < 1 ||
    monotonicMs === null ||
    !Number.isInteger(monotonicMs) ||
    monotonicMs < 0 ||
    tSeconds === null ||
    tSeconds < 0 ||
    !Array.isArray(raw.quality_flags) ||
    !raw.quality_flags.every((flag) => typeof flag === 'string')
  ) {
    return null;
  }

  const nullableNumberFields = [
    'voltage_raw_v',
    'current_raw_a',
    'temperature_raw_c',
    'voltage_cal_v',
    'current_cal_a',
    'conductance_s',
    'kappa_t_us_cm',
    'kappa_25_us_cm',
    'excitation_frequency_hz',
    'excitation_amplitude_v',
    'alpha_per_c',
    'cell_constant_cm_inv',
  ] as const;
  const numbers = {} as Record<(typeof nullableNumberFields)[number], number | null>;
  for (const key of nullableNumberFields) {
    const value = parseNullableNumber(raw, key);
    if (value === undefined) return null;
    numbers[key] = value;
  }
  const nullableStringFields = [
    'range_id',
    'compensation_model',
    'sensor_path_id',
    'calibration_id',
    'calibration_valid_until_utc',
  ] as const;
  const strings = {} as Record<(typeof nullableStringFields)[number], string | null>;
  for (const key of nullableStringFields) {
    const value = parseNullableString(raw, key);
    if (value === undefined) return null;
    strings[key] = value;
  }

  const flags = raw.quality_flags as string[];
  const incompleteRaw =
    numbers.voltage_raw_v === null ||
    numbers.current_raw_a === null ||
    numbers.temperature_raw_c === null;
  if (incompleteRaw && !flags.some((flag) => ['DROPOUT', 'OUT_OF_RANGE'].includes(flag))) {
    return null;
  }
  if (
    numbers.kappa_t_us_cm === null &&
    numbers.conductance_s !== null &&
    !flags.includes('UNCALIBRATED')
  ) {
    return null;
  }
  if (
    numbers.kappa_25_us_cm === null &&
    numbers.kappa_t_us_cm !== null &&
    !flags.some((flag) =>
      ['CALIBRATION_EXPIRED', 'TEMPERATURE_INVALID', 'COMPENSATION_UNAVAILABLE'].includes(flag),
    )
  ) {
    return null;
  }
  if (numbers.kappa_t_us_cm !== null && strings.calibration_id === null) return null;

  const frame: ExperimentFrame = {
    message_type: 'measurement',
    schema_version: '2.0',
    experiment_uid: experimentUid,
    seq_no: seqNo,
    timestamp_utc: timestampUtc,
    monotonic_ms: monotonicMs,
    t_seconds: tSeconds,
    status: 'running',
    ...numbers,
    ...strings,
    quality_flags: flags,
  };
  if (raw.concentration_mmol_l !== undefined) {
    const concentration = parseFiniteNumber(raw.concentration_mmol_l);
    if (concentration === null || concentration < 0) return null;
    frame.concentration_mmol_l = concentration;
  }
  return frame;
}
