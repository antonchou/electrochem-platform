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
 * 统计与拟合统一使用 κ25（Derived 层）；未校准（k25 为空）时回退 κ(T)（Calibrated 层）。
 */
export function ResultPanel({ pointsRef, status, count, experimentId, sampleId, api }: Props) {
  const stats = useMemo(() => {
    if (count === 0) return null;
    const pts = pointsRef.current;
    let sum = 0;
    let min = Infinity;
    let max = -Infinity;
    let valid = 0;
    for (const p of pts) {
      // 统计对象：κ25 优先，未校准时用 κ(T)；绝不使用原始 U/I/T 冒充电导率
      const value = p.k25 ?? p.kt;
      if (!Number.isFinite(value)) continue;
      sum += value;
      if (value < min) min = value;
      if (value > max) max = value;
      valid += 1;
    }
    if (valid === 0) return null;
    const mean = sum / valid;
    const last = pts[pts.length - 1];
    const lastValue = last ? (last.k25 ?? last.kt) : null;
    return {
      n: valid,
      mean,
      min,
      max,
      last: lastValue !== null && Number.isFinite(lastValue) ? lastValue : null,
    };
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
          <span>{stats.last === null ? '--' : `${stats.last.toFixed(2)} μS/cm`}</span>
        </div>
      ) : (
        <p className={styles.empty}>本轮实验暂无数据</p>
      )}

      <FitPanel api={api} points={pointsRef.current} btnTestId="btn-fit" />
    </section>
  );
}
