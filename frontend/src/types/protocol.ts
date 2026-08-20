/**
 * 与后端约定的实时数据协议（基准见《Web界面开发任务书》3.1）。
 * 本模块是协议的唯一事实来源：前端解析、校验、类型推导都从这里走。
 */

/** 实验状态 */
export type ExperimentStatus = 'idle' | 'running' | 'stopped' | 'error';

/** 实时数据帧：数据到达后的完整一帧 */
export interface ExperimentFrame {
  /** 实验开始后的时间，单位秒 */
  timestamp: number;
  /** 电导率，单位 μS/cm（单位由后端协议固定，前端必须明确显示） */
  ec: number;
  /** 温度，单位 °C */
  temperature: number;
  /** 状态字段 */
  status: ExperimentStatus;
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
  status: string;
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

/** 原始帧记录（历史查询/导出用） */
export interface RawFrame {
  id: number;
  sample_id: string | null;
  sensor_path_id: string;
  seq_no: number | null;
  timestamp_utc: string | null;
  monotonic_ms: number | null;
  t_seconds: number | null;
  ec_raw: number;
  temperature_raw: number;
  k25: number | null;
  quality_flags: string | null;
  status: string | null;
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
  /** μS/cm */
  ec: number;
  /** °C */
  tc: number;
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

  const hasMeasure =
    raw.timestamp !== undefined && raw.ec !== undefined && raw.temperature !== undefined;

  if (!hasMeasure) {
    return { status: status as ExperimentStatus } as StatusFrame;
  }

  const timestamp = Number(raw.timestamp);
  const ec = Number(raw.ec);
  const temperature = Number(raw.temperature);
  if (
    !Number.isFinite(timestamp) ||
    !Number.isFinite(ec) ||
    !Number.isFinite(temperature)
  ) {
    return null;
  }

  return { timestamp, ec, temperature, status: status as ExperimentStatus };
}
