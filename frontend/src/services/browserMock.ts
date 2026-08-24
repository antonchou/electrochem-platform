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
    if (this.connectDelay) {
      clearTimeout(this.connectDelay);
      this.connectDelay = null;
    }
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
      const resumed = this.status === 'stopped';
      this.status = 'running';
      if (!resumed) this.t0 = Date.now() / 1000;
      this.emit({ type: 'status', status: 'running' });
      this.startStream();
      return { ok: true, status: this.status, resumed };
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
    // 10 Hz 模拟 I–V 测量链路：由目标 κ25 反推原始电压/电流（与后端 Mock 一致）
    const base = 1413;
    const cellConstant = 1.0;
    const alpha = 0.02;
    const excitation = 1.0;
    this.timer = setInterval(() => {
      const t = Date.now() / 1000 - this.t0;
      const drift = Math.sin(t / 30) * 6;
      const noise = (Math.random() - 0.5) * 3;
      const kappa25 = +(base + drift + noise).toFixed(1);
      const temperature = +(25 + (Math.random() - 0.5) * 0.3).toFixed(2);
      // κ(T) = κ25·(1+α·(T-25))；G = κ(T)·1e-6/Kcell；I = G·U
      const kappaT = kappa25 * (1 + alpha * (temperature - 25));
      const g = +(kappaT * 1e-6 / cellConstant);
      const current = +(g * excitation);
      this.emit({
        type: 'message',
        frame: {
          timestamp: +t.toFixed(2),
          ec: kappa25,
          temperature,
          status: 'running',
          schema_version: 2,
          device_id: 'MOCK-IV-01',
          firmware_version: '0.1.0',
          range_id: 'WIDE',
          voltage_raw_v: excitation,
          current_raw_a: current,
          temperature_raw_c: temperature,
          conductance_s: g,
          kappa_t_us_cm: kappaT,
          kappa_25_us_cm: kappa25,
          quality_flags: 'SIMULATED',
        },
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
