import type { LabState, Point, Qc, ServerMessage } from './types.ts';

/**
 * 实时状态：对 WebSocket 有序消息的纯函数 reducer。
 *
 * 服务端保证：连接后第一条是快照（状态 + 当前曲线），之后按发生顺序推增量；
 * 状态消息总在它引起的读数之前。所以前端不需要另走 REST 拉数据、也不需要处理竞态：
 * - snapshot：整体替换
 * - state：当前测量变了（开始 / 结束）就清空曲线
 * - reading：只收属于当前模式的读数（measurement_id 与当前测量一致；监视时为 null）
 */

export const MAX_RECORDING_POINTS = 20_000;
export const MAX_MONITOR_POINTS = 600;

export interface LiveState {
  connected: boolean;
  lab: LabState | null;
  points: Point[];
  latest: Point | null;
  qc: Qc | null;
}

export const initialLive: LiveState = { connected: false, lab: null, points: [], latest: null, qc: null };

export type LiveAction = { kind: 'message'; message: ServerMessage } | { kind: 'connection'; connected: boolean };

function currentMeasurementId(state: LiveState): number | null {
  return state.lab?.measurement?.id ?? null;
}

function append(points: Point[], point: Point, cap: number): Point[] {
  const next = points.length >= cap ? points.slice(points.length - cap + 1) : points.slice();
  next.push(point);
  return next;
}

export function reduce(state: LiveState, action: LiveAction): LiveState {
  if (action.kind === 'connection') {
    return state.connected === action.connected ? state : { ...state, connected: action.connected };
  }
  const message = action.message;
  if (!state.connected) state = { ...state, connected: true }; // 收到任何消息都说明连着
  switch (message.type) {
    case 'snapshot': {
      const points = message.points;
      return {
        connected: true,
        lab: message.state,
        points,
        latest: points.length > 0 ? points[points.length - 1] : null,
        qc: message.qc,
      };
    }
    case 'state': {
      const changed = (message.state.measurement?.id ?? null) !== currentMeasurementId(state);
      return changed
        ? { ...state, lab: message.state, points: [], qc: null }
        : { ...state, lab: message.state };
    }
    case 'reading': {
      if (message.measurement_id !== currentMeasurementId(state)) return state; // 属于上一个模式的尾巴
      const recording = message.measurement_id !== null;
      return {
        ...state,
        points: append(state.points, message.point, recording ? MAX_RECORDING_POINTS : MAX_MONITOR_POINTS),
        latest: message.point,
        qc: recording ? (message.qc ?? state.qc) : null,
      };
    }
    default:
      return state;
  }
}

const TYPES = new Set(['snapshot', 'state', 'reading', 'heartbeat']);

/** 解析一条服务端消息；不认识的内容返回 null（忽略，不崩）。 */
export function parseMessage(text: string): ServerMessage | null {
  try {
    const value: unknown = JSON.parse(text);
    if (typeof value === 'object' && value !== null && TYPES.has((value as { type?: string }).type ?? '')) {
      return value as ServerMessage;
    }
  } catch {
    /* 非法 JSON：忽略 */
  }
  return null;
}
