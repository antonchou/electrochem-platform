import { useEffect, useReducer } from 'react';
import { initialLive, parseMessage, reduce, type LiveState } from './lib/live.ts';

const WATCHDOG_MS = 12_000; // 服务端每 5 s 至少发一条（心跳），超过 12 s 没消息就当连接已死
const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 5_000;

/** 连接 /ws 并把有序消息交给 reducer。断线自动重连；重连后服务端先发快照，状态自然对齐。 */
export function useLive(): LiveState {
  const [state, dispatch] = useReducer(reduce, initialLive);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retryTimer: number | undefined;
    let watchdog: number | undefined;
    let retryMs = RETRY_MIN_MS;
    let closed = false;

    const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;

    const armWatchdog = () => {
      window.clearTimeout(watchdog);
      watchdog = window.setTimeout(() => socket?.close(), WATCHDOG_MS);
    };

    const connect = () => {
      socket = new WebSocket(url);
      socket.onopen = () => {
        retryMs = RETRY_MIN_MS;
        armWatchdog();
      };
      socket.onmessage = (event) => {
        armWatchdog();
        const message = parseMessage(String(event.data));
        if (message) dispatch({ kind: 'message', message });
      };
      socket.onclose = () => {
        window.clearTimeout(watchdog);
        dispatch({ kind: 'connection', connected: false });
        if (closed) return;
        retryTimer = window.setTimeout(connect, retryMs);
        retryMs = Math.min(retryMs * 2, RETRY_MAX_MS);
      };
    };

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retryTimer);
      window.clearTimeout(watchdog);
      socket?.close();
    };
  }, []);

  return state;
}
