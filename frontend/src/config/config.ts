/**
 * 集中配置：后端地址、曲线参数。
 * 前端只连后端：用模拟源还是真实设备由后端 EC_DRIVER 决定，前端零改动。
 * 生产由 FastAPI 同源托管 dist（任意端口/反向代理均自动成立）；
 * 开发时 Vite :5173 默认连本机 :8000。可用 VITE_WS_URL / VITE_API_BASE 覆盖。
 * 详见 .env.example
 */

export interface AppConfig {
  /** WebSocket 实时流 + REST 控制（模拟源与真实后端共用这一套地址配置） */
  server: {
    wsUrl: string;
    apiBase: string;
  };
  chart: {
    /** 曲线保留的最大点数（超过则丢弃最旧点，防止 30 分钟长时间运行内存失控） */
    maxPoints: number;
    /** 曲线刷新的节流间隔（毫秒） */
    updateIntervalMs: number;
    /** 断线判定：超过该毫秒无数据判定为数据流超时 */
    staleThresholdMs: number;
  };
}

function envStr(key: string, fallback: string): string {
  const v = (import.meta.env as Record<string, string | undefined>)[key];
  return v && v.length > 0 ? v : fallback;
}

function defaultServerUrls(): { wsUrl: string; apiBase: string } {
  if (typeof window === 'undefined') {
    return { wsUrl: 'ws://localhost:8000/ws/stream', apiBase: 'http://localhost:8000' };
  }
  // 生产（build 产物）由 FastAPI 同源托管，任意端口/反代/HTTPS 自动成立；
  // 开发服务器没有后端，回落本机 :8000。
  // 判据用 import.meta.env.DEV（P1-2）：Vite 端口被占会漂移到 5174+，preview 是
  // 4173，按端口号判断都会静默失效把 API 指回页面自身 origin。
  const apiBase = import.meta.env.DEV
    ? `${window.location.protocol === 'https:' ? 'https' : 'http'}://${
        window.location.hostname || 'localhost'
      }:8000`
    : window.location.origin;
  return { wsUrl: `${apiBase.replace(/^http/, 'ws')}/ws/stream`, apiBase };
}

const defaultServer = defaultServerUrls();

export const config: AppConfig = {
  server: {
    wsUrl: envStr('VITE_WS_URL', defaultServer.wsUrl),
    apiBase: envStr('VITE_API_BASE', defaultServer.apiBase),
  },
  chart: {
    maxPoints: 20000,
    updateIntervalMs: 200,
    staleThresholdMs: 3000,
  },
};
