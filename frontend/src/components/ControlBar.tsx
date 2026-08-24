import styles from './ControlBar.module.css';

interface Props {
  busy: boolean;
  canStart: boolean;
  canStop: boolean;
  canReset: boolean;
  paused: boolean;
  onStart: () => void;
  onStop: () => void;
  onReset: () => void;
}

/** 实验控制按钮组：运行中禁用"开始"防止重复触发；停止后「开始」续跑，重新开始才开新实验 */
export function ControlBar({
  busy,
  canStart,
  canStop,
  canReset,
  paused,
  onStart,
  onStop,
  onReset,
}: Props) {
  return (
    <div className={styles.bar}>
      <button
        type="button"
        data-testid="btn-start"
        className={`${styles.btn} ${styles.primary}`}
        onClick={onStart}
        disabled={!canStart || busy}
      >
        {busy ? '处理中…' : paused ? '继续实验' : '开始实验'}
      </button>
      <button
        type="button"
        data-testid="btn-stop"
        className={styles.btn}
        onClick={onStop}
        disabled={!canStop || busy}
      >
        停止
      </button>
      <button
        type="button"
        data-testid="btn-reset"
        className={styles.btn}
        onClick={onReset}
        disabled={!canReset || busy}
      >
        重新开始
      </button>
    </div>
  );
}
