import type { ExperimentStatus } from '../types/protocol';
import styles from './StatusBadge.module.css';

const LABEL: Record<ExperimentStatus, string> = {
  idle: '空闲',
  running: '运行中',
  stopped: '已停止',
  error: '异常',
};

/** 实验状态徽标：Running / Stopped / Idle / Error 清晰区分（任务书 §4.3） */
export function StatusBadge({ status }: { status: ExperimentStatus }) {
  return (
    <span className={`${styles.badge} ${styles[status]}`} data-testid="experiment-status">
      {LABEL[status]}
    </span>
  );
}
