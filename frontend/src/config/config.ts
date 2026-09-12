/**
 * 集中配置：后端地址、数据源模式、曲线参数。
 * 切换「模拟数据源」与「真实后端」只需改环境变量，核心业务代码零改动。
 * 生产由 FastAPI 同源托管 dist（任意端口/反向代理均自动成立）；
 * 开发时 Vite :5173 默认连本机 :8000。可用 VITE_WS_URL / VITE_API_BASE 覆盖。
 * 详见 .env.example
 */

export type DataSourceMode = 'server' | 'browser';

export interface AppConfig {
  /** 数据源模式 */
  dataSource: DataSourceMode;
  /** server 模式：WebSocket 实时流 + REST 控制（模拟源与真实后端共用这一套地址配置） */
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
  // 生产由 FastAPI 同源托管 dist（任意端口/反代/HTTPS 都成立，T-11）；
  // 仅 Vite 开发服务器（:5173）没有后端，回落到本机 :8000。
  const isViteDevServer = window.location.port === '5173';
  const apiBase = isViteDevServer
    ? `${window.location.protocol === 'https:' ? 'https' : 'http'}://${
        window.location.hostname || 'localhost'
      }:8000`
    : window.location.origin;
  return { wsUrl: `${apiBase.replace(/^http/, 'ws')}/ws/stream`, apiBase };
}

const defaultServer = defaultServerUrls();

export const config: AppConfig = {
  dataSource: envStr('VITE_DATA_SOURCE', 'server') === 'browser' ? 'browser' : 'server',
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
