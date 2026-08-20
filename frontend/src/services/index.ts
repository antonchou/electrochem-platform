import { config } from '../config/config';
import type {
  ClientEvent,
  ControlAction,
  ControlResponse,
  ExperimentStartOptions,
} from '../types/protocol';
import { WebSocketClient } from './websocketClient';
import { ApiClient } from './apiClient';
import { BrowserMockSource } from './browserMock';

/**
 * 实验数据桥：统一「实时流 + 控制 + 历史查询」的入口。
 * - server 模式：WebSocketClient（实时流）+ ApiClient（REST 控制/历史）
 * - browser 模式：BrowserMockSource（纯浏览器模拟）
 * 上层业务只依赖本接口，切换数据源不改业务代码（任务书 §3 约束）。
 */
export interface ExperimentBridge {
  connect(): void;
  disconnect(): void;
  subscribe(listener: (ev: ClientEvent) => void): () => void;
  control(action: ControlAction, options?: ExperimentStartOptions): Promise<ControlResponse>;
  /** 历史/导出 API（仅 server 模式可用，browser 模式为 null） */
  readonly api: ApiClient | null;
  readonly mode: 'server' | 'browser';
}

class ServerBridge implements ExperimentBridge {
  readonly mode = 'server' as const;
  readonly api: ApiClient;
  private ws: WebSocketClient;

  constructor() {
    this.ws = new WebSocketClient(config.server.wsUrl);
    this.api = new ApiClient(config.server.apiBase);
  }

  connect(): void {
    this.ws.connect();
  }

  disconnect(): void {
    this.ws.disconnect();
  }

  subscribe(listener: (ev: ClientEvent) => void): () => void {
    return this.ws.subscribe(listener);
  }

  control(action: ControlAction, options?: ExperimentStartOptions): Promise<ControlResponse> {
    return this.api.control(action, options);
  }
}

class BrowserBridge implements ExperimentBridge {
  readonly mode = 'browser' as const;
  readonly api = null;
  private mock = new BrowserMockSource();

  connect(): void {
    this.mock.connect();
  }

  disconnect(): void {
    this.mock.disconnect();
  }

  subscribe(listener: (ev: ClientEvent) => void): () => void {
    return this.mock.subscribe(listener);
  }

  control(action: ControlAction, _options?: ExperimentStartOptions): Promise<ControlResponse> {
    return this.mock.control(action);
  }
}

let bridge: ExperimentBridge | null = null;

/** 获取全局唯一的桥（浏览器中单例）。 */
export function getBridge(): ExperimentBridge {
  if (!bridge) {
    bridge = config.dataSource === 'browser' ? new BrowserBridge() : new ServerBridge();
  }
  return bridge;
}
