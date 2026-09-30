import { useMemo, useState } from 'react';
import type { ApiClient } from '../services/apiClient';
import type { ExperimentSummary, FitAxis, FitResponse } from '../types/protocol';
import {
  calibrationCandidate,
  calibrationDataPoints,
  selectionKey,
  type CalibrationCandidate,
} from '../lib/calibration';
import { FitPanel } from './FitPanel';
import styles from './CalibrationPanel.module.css';

interface Props {
  api: ApiClient;
  experiments: ExperimentSummary[];
}

const CONCENTRATION_ONLY: FitAxis[] = ['concentration'];

/**
 * 跨实验浓度标定（09-30 审查 #4）：单个实验只有一种浓度，浓度轴拟合只能跨实验做。
 * 勾选 ≥3 个不同浓度的已停止实验，每个实验取一个点（QC PASS 用代表值、WARN 用窗口中位数），
 * 拟合 κ25–浓度（线性标定 / 二次 / Kohlrausch）。取点与准入以服务端为准，
 * 报告写 data/derived/calibration_*.json（含成员实验，可溯源）。
 */
export function CalibrationPanel({ api, experiments }: Props) {
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [derivedPath, setDerivedPath] = useState<string | null>(null);

  const rows = useMemo(
    () => experiments.map((e) => ({ e, check: calibrationCandidate(e) })),
    [experiments],
  );
  const chosen: CalibrationCandidate[] = useMemo(
    () =>
      rows.flatMap(({ check }) =>
        check.ok && selected.has(check.candidate.id) ? [check.candidate] : [],
      ),
    [rows, selected],
  );
  const points = useMemo(() => calibrationDataPoints(chosen), [chosen]);
  const distinct = new Set(chosen.map((c) => c.concentration)).size;

  const toggle = (id: number) => {
    setDerivedPath(null);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const fitRunner = async (
    _points: [number, number][],
    models: string[],
    _axis: FitAxis,
  ): Promise<FitResponse> => {
    const res = await api.fitCalibration(
      chosen.map((c) => c.id),
      models,
    );
    setDerivedPath(res.derived_path);
    return res;
  };

  return (
    <div className={styles.wrap} data-testid="calibration-panel">
      <p className={styles.hint}>
        勾选 ≥3 个不同浓度的已停止实验，每个实验取一个点：QC PASS 用代表值，WARN
        用窗口中位数。QC FAIL、未判定或没填浓度的实验不能选。
      </p>
      {rows.length === 0 ? (
        <p className={styles.hint}>暂无历史实验。</p>
      ) : (
        <table className="data-table" data-testid="calibration-table">
          <thead>
            <tr>
              <th aria-label="选择" />
              <th>实验</th>
              <th>样品</th>
              <th>浓度 (mmol/L)</th>
              <th>κ25 (μS/cm)</th>
              <th>QC</th>
              <th>取值</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ e, check }) => (
              <tr key={e.id} className={check.ok ? undefined : styles.disabledRow}>
                <td>
                  <input
                    type="checkbox"
                    checked={check.ok && selected.has(e.id)}
                    disabled={!check.ok}
                    onChange={() => toggle(e.id)}
                    aria-label={`选择实验 ${e.experiment_id}`}
                    data-testid={`calib-check-${e.id}`}
                  />
                </td>
                <td>{e.experiment_id}</td>
                <td>{e.sample_id ?? '--'}</td>
                <td>{e.concentration_mmol_l ?? '--'}</td>
                <td>{check.ok ? check.candidate.kappa25.toFixed(2) : '--'}</td>
                <td>{e.qc_status ?? '--'}</td>
                <td>
                  {check.ok
                    ? check.candidate.source === 'representative'
                      ? 'QC 代表值'
                      : '中位数（QC WARN）'
                    : check.reason}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className={styles.summary} data-testid="calibration-summary">
        已选 {chosen.length} 个实验 · {distinct} 种浓度
        {distinct < 3 ? '（至少需要 3 种）' : ''}
      </div>

      {chosen.length > 0 && (
        <FitPanel
          key={selectionKey(chosen.map((c) => c.id))}
          api={api}
          points={points}
          testIdPrefix="calib-fit"
          axes={CONCENTRATION_ONLY}
          fitRunner={fitRunner}
        />
      )}

      {derivedPath && (
        <div className={styles.saved} data-testid="calibration-saved">
          标定报告已保存：{derivedPath}
        </div>
      )}
    </div>
  );
}
