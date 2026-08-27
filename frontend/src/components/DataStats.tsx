import { formatExcitationHz } from '../lib/units';
import styles from './DataStats.module.css';

interface Props {
  pointCount: number;
  durationSec: number;
  sampleRateHz?: number | null;
  excitationFreqHz?: number | null;
  excitationAmpV?: number | null;
}

/** 数据状态：采样点数、时长，以及后端提供的激励信息 */
export function DataStats({
  pointCount,
  durationSec,
  sampleRateHz,
  excitationFreqHz,
  excitationAmpV,
}: Props) {
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
      {sampleRateHz != null && Number.isFinite(sampleRateHz) && (
        <div className={styles.item}>
          <span className={styles.label}>采样率</span>
          <span className={styles.value} data-testid="stat-rate">
            {sampleRateHz.toFixed(1)} Hz
          </span>
        </div>
      )}
      {excitationAmpV != null && Number.isFinite(excitationAmpV) && (
        <div className={styles.item}>
          <span className={styles.label}>激励幅值</span>
          <span className={styles.value} data-testid="stat-exc-amp">
            {excitationAmpV.toFixed(3)} V
          </span>
        </div>
      )}
      {excitationFreqHz != null && Number.isFinite(excitationFreqHz) && (
        <div className={styles.item}>
          <span className={styles.label}>激励</span>
          <span className={styles.value} data-testid="stat-exc-freq">
            {formatExcitationHz(excitationFreqHz)}
          </span>
        </div>
      )}
    </div>
  );
}
