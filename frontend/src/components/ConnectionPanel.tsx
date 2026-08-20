import type { ConnectionStatus } from '../types/protocol';
import styles from './ConnectionPanel.module.css';

const LABEL: Record<ConnectionStatus, string> = {
  idle: '未连接',
  connecting: '连接中…',
  connected: '已连接',
  reconnecting: '重连中…',
  disconnected: '已断开',
};

interface Props {
  status: ConnectionStatus;
  mode: 'server' | 'browser';
  onReconnect: () => void;
}

/** 连接状态面板：已连接 / 已断开 / 重连中 清晰区分；断线后可手动重连（任务书 §2 / F08 F09） */
export function ConnectionPanel({ status, mode, onReconnect }: Props) {
  const offline = status === 'disconnected' || status === 'reconnecting';
  return (
    <div className={styles.panel} data-testid="connection-panel">
      <span className={`${styles.dot} ${styles[status]}`} />
      <span className={`${styles.text} ${styles[status]}`} data-testid="connection-status">
        {LABEL[status]}
      </span>
      <span className={styles.mode}>数据源：{mode === 'browser' ? '浏览器模拟' : '后端(WS)'}</span>
      {offline && (
        <button type="button" className={styles.reconnect} onClick={onReconnect} data-testid="btn-reconnect">
          手动重连
        </button>
      )}
    </div>
  );
}
