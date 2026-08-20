/**
 * 集中配置：后端地址、数据源模式、曲线参数。
 * 切换「模拟数据源」与「真实后端」只需改环境变量，核心业务代码零改动。
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

export const config: AppConfig = {
  dataSource: envStr('VITE_DATA_SOURCE', 'server') === 'browser' ? 'browser' : 'server',
  server: {
    wsUrl: envStr('VITE_WS_URL', 'ws://localhost:8000/ws/stream'),
    apiBase: envStr('VITE_API_BASE', 'http://localhost:8000'),
  },
  chart: {
    maxPoints: 20000,
    updateIntervalMs: 200,
    staleThresholdMs: 3000,
  },
};
