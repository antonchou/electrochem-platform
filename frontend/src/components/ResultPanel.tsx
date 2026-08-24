import { useEffect, useMemo, useState } from 'react';
import type { DataPoint, ExperimentDetail, ExperimentStatus, SampleSummary } from '../types/protocol';
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

function qcBadgeClass(status: string | null | undefined): string {
  if (status === 'PASS') return `${styles.qcBadge} ${styles.qcPass}`;
  if (status === 'WARN') return `${styles.qcBadge} ${styles.qcWarn}`;
  if (status === 'FAIL') return `${styles.qcBadge} ${styles.qcFail}`;
  return `${styles.qcBadge} ${styles.qcNone}`;
}

function fmtNum(value: number | null | undefined, digits = 2): string {
  return value != null && Number.isFinite(value) ? value.toFixed(digits) : '--';
}

/**
 * 结果区域：QC/代表值 + 基础统计 + 样品溯源/导出 + 化学公式拟合。
 */
export function ResultPanel({ pointsRef, status, count, experimentId, sampleId, api }: Props) {
  const [detail, setDetail] = useState<ExperimentDetail | null>(null);

  useEffect(() => {
    setDetail(null);
  }, [experimentId]);

  useEffect(() => {
    if (!api || experimentId == null || status === 'running') return;
    let cancelled = false;
    api
      .getExperiment(experimentId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch(() => {
        if (!cancelled) setDetail(null);
      });
    return () => {
      cancelled = true;
    };
  }, [api, experimentId, status, count]);

  const sample: SampleSummary | undefined = detail?.samples?.[0];

  const stats = useMemo(() => {
    if (count === 0) return null;
    const pts = pointsRef.current;
    let sum = 0;
    let min = Infinity;
    let max = -Infinity;
    const values = pts.map((p) => p.ec).filter((v): v is number => v !== null && Number.isFinite(v));
    if (values.length === 0) return null;
    for (const v of values) {
      sum += v;
      if (v < min) min = v;
      if (v > max) max = v;
    }
    const mean = sum / values.length;
    const last = values[values.length - 1];
    return { n: values.length, mean, min, max, last };
  }, [status, count, pointsRef]);

  const pointsForFit = useMemo(() => {
    const pts = pointsRef.current.slice();
    const c = sample?.concentration_mmol_l;
    if (c == null || !Number.isFinite(c)) return pts;
    return pts.map((p) => ({ ...p, concentration: p.concentration ?? c }));
  }, [count, status, sample, pointsRef]);

  if (status === 'running') return null;

  const qcStatus = sample?.qc_status ?? null;
  const concentration = sample?.concentration_mmol_l;

  return (
    <section className={styles.panel} data-testid="result-panel">
      <div className={styles.headRow}>
        <h2 className={styles.heading}>实验结果</h2>
        {experimentId && api && (
          <div className={styles.actions}>
            <span className={styles.sampleTag} data-testid="result-sample">
              样品：{sampleId || sample?.sample_id || '--'}
            </span>
            <span className={styles.sampleTag} data-testid="result-concentration">
              浓度：{concentration != null ? `${concentration} mmol/L` : '--'}
            </span>
            <a
              className={styles.download}
              href={api.exportCsvUrl(experimentId)}
              download
              data-testid="btn-export-current"
              onClick={(event) => {
                event.preventDefault();
                void api
                  .downloadExport(api.exportCsvUrl(experimentId), `experiment_${experimentId}.csv`)
                  .catch((err) => {
                    console.error(err);
                  });
              }}
            >
              导出 CSV
            </a>
          </div>
        )}
      </div>

      <div className={styles.qcRow} data-testid="result-qc">
        <span className={styles.label}>QC</span>
        <span className={qcBadgeClass(qcStatus)} data-testid="result-qc-status">
          {qcStatus ?? '未判定'}
        </span>
        <span className={styles.label}>代表值</span>
        <span data-testid="result-qc-value">
          {sample?.representative_value != null
            ? `${fmtNum(sample.representative_value)} μS/cm`
            : '--'}
        </span>
        <span className={styles.label}>中位数</span>
        <span>
          {sample?.k25_median != null ? `${fmtNum(sample.k25_median)} μS/cm` : '--'}
        </span>
        <span className={styles.label}>原因</span>
        <span data-testid="result-qc-reason">{sample?.qc_reason ?? '--'}</span>
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

      <FitPanel
        key={`${experimentId ?? 'none'}-${count}`}
        api={api}
        points={pointsForFit}
        btnTestId="btn-fit"
      />
    </section>
  );
}
