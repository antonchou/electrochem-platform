import type { ClientEvent, ConnectionStatus, ExperimentFrame, ServerMessage } from '../types/protocol';
import { isDebugBurstFrame, parseServerMessage } from '../types/protocol';
import { config } from '../config/config';

export type { ClientEvent, ConnectionStatus };

export interface DataClient {
  connect(): void;
  disconnect(): void;
  subscribe(listener: (ev: ClientEvent) => void): () => void;
}

/**
 * 原生 WebSocket 客户端。
 * 职责：连接、协议解析与校验、断线检测、自动重连（指数退避）、超时看门狗。
 * 不包含任何 UI 逻辑；UI 只通过订阅事件消费。
 */
export class WebSocketClient implements DataClient {
  private ws: WebSocket | null = null;
  private listeners = new Set<(ev: ClientEvent) => void>();
  private status: ConnectionStatus = 'idle';
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private watchdogTimer: ReturnType<typeof setInterval> | null = null;
  private lastMessageAt = 0;
  private manualClosed = false;
  /** 最近收到消息所反映的实验是否运行中（看门狗只在 running 时检查，P2-5） */
  private running = false;

  constructor(private readonly url: string) {}

  private emit(ev: ClientEvent): void {
    this.listeners.forEach((l) => {
      try {
        l(ev);
      } catch {
        // 监听器内部异常不得影响其它监听器
      }
    });
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.status !== status) {
      this.status = status;
      this.emit({ type: 'connection', status });
    }
  }

  connect(): void {
    this.manualClosed = false;
    // P2-6 修复：手动重连前取消已排队的自动重连定时器，并关闭可能存在的旧连接，
    // 避免重连后旧定时器再开一个连接，造成双 WebSocket 重复收帧。
    this.clearTimers();
    if (this.ws && this.ws.readyState !== WebSocket.CLOSED) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.open();
  }

  disconnect(): void {
    this.manualClosed = true;
    this.clearTimers();
    if (this.ws) {
      // 断开由用户主动触发，不再触发自动重连
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.setStatus('idle');
  }

  subscribe(listener: (ev: ClientEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private open(): void {
    if (this.manualClosed) return;
    this.setStatus(this.reconnectAttempts > 0 ? 'reconnecting' : 'connecting');

    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url);
    } catch {
      this.emit({ type: 'error', message: '无法建立 WebSocket 连接' });
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.running = false; // 新连接未收到数据前，看门狗不检查（避免空闲误报）
      this.setStatus('connected');
      this.armWatchdog();
    };

    ws.onmessage = (event: MessageEvent) => {
      this.lastMessageAt = Date.now();
      const parsed: ServerMessage | null = parseServerMessage(event.data);
      if (!parsed) {
        // 坏数据：丢弃该帧并提示，页面不崩溃（F10）
        this.emit({ type: 'error', message: '收到非法数据帧，已忽略' });
        return;
      }
      if (parsed.message_type === 'measurement') {
        if (!isDebugBurstFrame(parsed)) this.running = parsed.status === 'running';
        this.emit({ type: 'message', frame: parsed as ExperimentFrame });
      } else {
        this.running = parsed.status === 'running';
        this.emit({
          type: 'status',
          status: parsed.status,
          experimentUid: parsed.experiment_uid,
        });
      }
    };

    ws.onerror = () => {
      this.emit({ type: 'error', message: 'WebSocket 连接出错' });
    };

    ws.onclose = () => {
      if (this.manualClosed) return;
      this.setStatus('disconnected');
      this.emit({ type: 'error', message: 'WebSocket 连接已断开' });
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.manualClosed || this.reconnectTimer !== null) return;
    this.reconnectAttempts += 1;
    const delay = Math.min(1000 * this.reconnectAttempts, 5000);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.open();
    }, delay);
  }

  /**
   * 看门狗：实验运行中连接已建立但持续无数据（超过 staleThresholdMs）→ 提示数据流超时。
   * 仅在 running 时检查（P2-5）：空闲/停止/刚连接未运行时不检查，避免误报。
   */
  private armWatchdog(): void {
    this.lastMessageAt = Date.now();
    this.clearWatchdog();
    this.watchdogTimer = setInterval(() => {
      if (this.status !== 'connected') return;
      if (!this.running) return;
      if (Date.now() - this.lastMessageAt > config.chart.staleThresholdMs) {
        this.emit({ type: 'error', message: `数据流超时（${config.chart.staleThresholdMs / 1000} 秒无数据）` });
      }
    }, 1000);
  }

  private clearWatchdog(): void {
    if (this.watchdogTimer !== null) {
      clearInterval(this.watchdogTimer);
      this.watchdogTimer = null;
    }
  }

  private clearTimers(): void {
    this.clearWatchdog();
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}
