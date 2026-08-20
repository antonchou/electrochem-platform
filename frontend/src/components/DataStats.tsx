import styles from './DataStats.module.css';

interface Props {
  pointCount: number;
  durationSec: number;
}

/** 数据状态：当前采样点数 + 运行时长（任务书 §2） */
export function DataStats({ pointCount, durationSec }: Props) {
  return (
    <div className={styles.stats} data-testid="data-stats">
      <div className={styles.item}>
        <span className={styles.label}>采样点数</span>
        <span className={styles.value} data-testid="stat-count">
          {pointCount}
        </span>
      </div>
      <div className={styles.item}>
        <span className={styles.label}>运行时长</span>
        <span className={styles.value} data-testid="stat-duration">
          {durationSec.toFixed(1)} s
        </span>
      </div>
    </div>
  );
}
