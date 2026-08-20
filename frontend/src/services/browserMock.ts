import type { ClientEvent, ControlAction, ControlResponse, ExperimentStatus } from '../types/protocol';
import type { DataClient } from './websocketClient';

/**
 * 浏览器内置模拟数据源（dataSource=browser 时使用，无需任何后端）。
 * 与 WebSocket 客户端实现相同的接口（DataClient + control），
 * 保证上层业务逻辑（hooks/页面）完全复用、零改动。
 */
export class BrowserMockSource implements DataClient {
  private listeners = new Set<(ev: ClientEvent) => void>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private status: ExperimentStatus = 'idle';
  private t0 = 0;
  private connectDelay: ReturnType<typeof setTimeout> | null = null;

  private emit(ev: ClientEvent): void {
    this.listeners.forEach((l) => {
      try {
        l(ev);
      } catch {
        /* ignore */
      }
    });
  }

  connect(): void {
    this.emit({ type: 'connection', status: 'connecting' });
    // 模拟连接耗时，并演示断线→重连状态变化
    this.connectDelay = setTimeout(() => {
      this.emit({ type: 'connection', status: 'connected' });
      if (this.status === 'running') this.startStream();
    }, 600);
  }

  disconnect(): void {
    if (this.connectDelay) clearTimeout(this.connectDelay);
    this.stopStream();
    this.emit({ type: 'connection', status: 'idle' });
  }

  subscribe(listener: (ev: ClientEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async control(action: ControlAction): Promise<ControlResponse> {
    if (action === 'start') {
      if (this.status === 'running') {
        return { ok: false, status: this.status, message: '实验已在进行中' };
      }
      this.status = 'running';
      this.t0 = Date.now() / 1000;
      this.startStream();
      return { ok: true, status: this.status };
    }
    if (action === 'stop') {
      this.stopStream();
      this.status = 'stopped';
      this.emit({ type: 'status', status: 'stopped' });
      return { ok: true, status: this.status };
    }
    // reset
    this.stopStream();
    this.status = 'idle';
    this.emit({ type: 'status', status: 'idle' });
    return { ok: true, status: this.status };
  }

  private startStream(): void {
    if (this.timer) return;
    // 10 Hz 模拟电导率读数：稳定基值 + 噪声 + 轻微漂移
    const base = 1413;
    this.timer = setInterval(() => {
      const t = Date.now() / 1000 - this.t0;
      const drift = Math.sin(t / 30) * 6;
      const noise = (Math.random() - 0.5) * 3;
      const ec = +(base + drift + noise).toFixed(1);
      const temperature = +(25 + (Math.random() - 0.5) * 0.3).toFixed(2);
      this.emit({
        type: 'message',
        frame: { timestamp: +t.toFixed(2), ec, temperature, status: 'running' },
      });
    }, 100);
  }

  private stopStream(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }
}
