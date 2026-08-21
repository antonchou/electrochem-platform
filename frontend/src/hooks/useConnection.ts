import { useCallback, useEffect, useState } from 'react';
import type { ConnectionStatus } from '../types/protocol';
import type { ExperimentBridge } from '../services';

/**
 * 连接状态管理：自动连接、手动重连、断线错误收集。
 * 断线检测在 WebSocketClient 内完成（close 事件 + 3 秒看门狗），
 * 这里只负责把状态同步到 React。
 */
export function useConnection(bridge: ExperimentBridge) {
  const [connStatus, setConnStatus] = useState<ConnectionStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const unsub = bridge.subscribe((ev) => {
      if (ev.type === 'connection') {
        setConnStatus(ev.status);
        if (ev.status === 'connected') setError(null);
      }
      if (ev.type === 'error') {
        setError(ev.message);
      }
    });
    bridge.connect();
    return () => {
      unsub();
      bridge.disconnect();
    };
  }, [bridge]);

  const manualReconnect = useCallback(() => {
    setError(null);
    bridge.connect();
  }, [bridge]);

  return { connStatus, error, setError, manualReconnect };
}
