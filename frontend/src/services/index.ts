import { config } from '../config/config';
import type {
  ClientEvent,
  ControlAction,
  ControlResponse,
  ExperimentStartOptions,
} from '../types/protocol';
import { WebSocketClient } from './websocketClient';
import { ApiClient } from './apiClient';

/**
 * 实验数据桥：统一「实时流 + 控制 + 历史查询」的入口。
 * 实时流走 WebSocketClient，控制与历史走 ApiClient。数据来自模拟源还是真实设备由后端
 * EC_DRIVER 决定，前端不区分（浏览器内置模拟模式已于 2026-10-01 删除：它是与后端模拟器
 * 并行的第二套实现，行为会分叉，也没有测试覆盖）。
 */
export interface ExperimentBridge {
  connect(): void;
  disconnect(): void;
  subscribe(listener: (ev: ClientEvent) => void): () => void;
  control(action: ControlAction, options?: ExperimentStartOptions): Promise<ControlResponse>;
  /** 历史 / 导出 / 拟合 API */
  readonly api: ApiClient;
}

class ServerBridge implements ExperimentBridge {
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

let bridge: ExperimentBridge | null = null;

/** 获取全局唯一的桥（浏览器中单例）。 */
export function getBridge(): ExperimentBridge {
  if (!bridge) bridge = new ServerBridge();
  return bridge;
}
