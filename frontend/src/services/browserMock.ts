import type { ClientEvent, ControlAction, ControlResponse, ExperimentStatus } from '../types/protocol';
import type { DataClient } from './websocketClient';

/**
 * 电极 I–V 链路模拟（与后端 MockDevice 同一套计算）：
 * 只模拟原始 U/I/T；G/κ(T)/κ25 由计算链还原（与 app.measurement/app.calibration 一致）。
 * 默认带有效校准：Kcell=1 cm⁻¹、α=0.02、linear 温补。
 */
const BASE_K25 = 1413; // μS/cm（目标 κ25）
const BASE_TEMP = 25; // °C
const EXCITATION_AMPLITUDE_V = 0.4; // V
const KCELL_CM_INV = 1.0; // cm⁻¹
const ALPHA_PER_C = 0.02; // 1/°C
const COMPENSATION_MODEL = 'linear';
const RANGE_ID = 'R_100R_10K';
const SENSOR_PATH_ID = 'EC_IV_CELL_BROWSER';
const CALIBRATION_ID = 'CAL_BROWSER_DEMO';

/** 与后端 calibration.compute_iv 相同的计算链（纯 JS 副本，linear 温补）。 */
function computeIV(
  voltageRawV: number,
  currentRawA: number,
  temperatureRawC: number,
): { g: number | null; kt: number | null; k25: number | null; flags: string[] } {
  const g = voltageRawV === 0 ? null : currentRawA / voltageRawV;
  if (g === null) return { g, kt: null, k25: null, flags: ['OPEN_CIRCUIT'] };
  const kt = g * KCELL_CM_INV * 1e6;
  const denom = 1 + ALPHA_PER_C * (temperatureRawC - 25);
  const k25 = kt / denom;
  const flags: string[] = [];
  if (!Number.isFinite(denom)) flags.push('COMPENSATION_UNAVAILABLE');
  return { g, kt, k25, flags };
}

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
      // 与 server 模式一致：start 时先广播 running 状态帧，
      // 触发 useRealtimeData 清空上一轮缓冲，避免 stop→start 新旧数据混合
      this.emit({ type: 'status', status: 'running' });
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
    // 10 Hz 模拟电极 I–V 原始读数：稳定基值 + 噪声 + 轻微漂移
    this.timer = setInterval(() => {
      const t = Date.now() / 1000 - this.t0;
      const drift = Math.sin(t / 30) * 6;
      const noise = (Math.random() - 0.5) * 3;
      const targetK25 = BASE_K25 + drift + noise;
      const temperature = +(BASE_TEMP + (Math.random() - 0.5) * 0.3).toFixed(2);
      // 由目标 κ25 反推原始 U/I（与后端 MockDevice.read 一致：R=Kcell/κ，I=U/R）
      const voltage = +EXCITATION_AMPLITUDE_V.toFixed(4);
      const resistance = KCELL_CM_INV / (targetK25 * 1e-6);
      const current = +(voltage / resistance).toFixed(9);

      const { g, kt, k25, flags } = computeIV(voltage, current, temperature);
      this.emit({
        type: 'message',
        frame: {
          schema_version: '2.0',
          timestamp: +t.toFixed(2),
          status: 'running',
          // Raw（不可变原始量）
          voltage_raw_v: voltage,
          current_raw_a: current,
          temperature_raw_c: temperature,
          // Calibrated / Derived
          conductance_s: g ?? undefined,
          kappa_t_us_cm: kt ?? undefined,
          kappa_25_us_cm: k25 ?? undefined,
          // Configuration / Trace
          excitation_frequency_hz: 1000,
          excitation_amplitude_v: EXCITATION_AMPLITUDE_V,
          range_id: RANGE_ID,
          sensor_path_id: SENSOR_PATH_ID,
          calibration_id: CALIBRATION_ID,
          compensation_model: COMPENSATION_MODEL,
          alpha_per_c: ALPHA_PER_C,
          // Quality
          quality_flags: ['SIMULATED', ...flags],
          // V1 废弃兼容别名
          ec: kt ?? undefined,
          temperature,
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
