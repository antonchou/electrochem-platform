import { useMemo } from 'react';
import type { DataPoint, ExperimentStatus } from '../types/protocol';
import type { ApiClient } from '../services/apiClient';
import { FitPanel } from './FitPanel';
import styles from './ResultPanel.module.css';

interface Props {
  pointsRef: React.MutableRefObject<DataPoint[]>;
  status: ExperimentStatus;
  count: number;
  /** Phase 7：当前实验 DB id（用于导出） */
  experimentId: number | null;
  sampleId: string;
  api: ApiClient | null;
}

/**
 * 结果区域：基础统计 + 样品溯源/导出 + 化学公式拟合（复用 FitPanel）。
 */
export function ResultPanel({ pointsRef, status, count, experimentId, sampleId, api }: Props) {
  const stats = useMemo(() => {
    if (count === 0) return null;
    const pts = pointsRef.current;
    let sum = 0;
    let min = Infinity;
    let max = -Infinity;
    for (const p of pts) {
      sum += p.ec;
      if (p.ec < min) min = p.ec;
      if (p.ec > max) max = p.ec;
    }
    const mean = sum / pts.length;
    const last = pts[pts.length - 1].ec;
    return { n: pts.length, mean, min, max, last };
  }, [status, count, pointsRef]);

  if (status === 'running') return null;

  return (
    <section className={styles.panel} data-testid="result-panel">
      <div className={styles.headRow}>
        <h2 className={styles.heading}>实验结果</h2>
        {experimentId && api && (
          <div className={styles.actions}>
            <span className={styles.sampleTag} data-testid="result-sample">
              样品：{sampleId || '--'}
            </span>
            <a
              className={styles.download}
              href={api.exportCsvUrl(experimentId)}
              download
              data-testid="btn-export-current"
            >
              导出 CSV
            </a>
          </div>
        )}
      </div>

      {stats ? (
        <div className={styles.grid}>
          <span className={styles.label}>样本数</span>
          <span>{stats.n}</span>
          <span className={styles.label}>均值</span>
          <span>{stats.mean.toFixed(2)} μS/cm</span>
          <span className={styles.label}>最小值</span>
          <span>{stats.min.toFixed(2)} μS/cm</span>
          <span className={styles.label}>最大值</span>
          <span>{stats.max.toFixed(2)} μS/cm</span>
          <span className={styles.label}>末值</span>
          <span>{stats.last.toFixed(2)} μS/cm</span>
        </div>
      ) : (
        <p className={styles.empty}>本轮实验暂无数据</p>
      )}

      <FitPanel api={api} points={pointsRef.current} btnTestId="btn-fit" />
    </section>
  );
}
