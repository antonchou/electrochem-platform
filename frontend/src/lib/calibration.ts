import type { DataPoint, ExperimentSummary } from '../types/protocol';

/**
 * 跨实验浓度标定的选点规则（09-30 审查 #4）。与后端 storage.calibration_points 一致，
 * 前端只用来决定哪些实验可勾选；最终取点与准入以服务端为准。
 *
 * 一实验一点：QC PASS 取代表值（窗口均值），WARN 没有代表值则取窗口中位数；
 * FAIL / 未判定 / 未填浓度 / 未正常停止的实验不能作标定点。
 */
export type CalibrationSource = 'representative' | 'median';

export interface CalibrationCandidate {
  id: number;
  experimentUid: string;
  sampleId: string;
  concentration: number;
  kappa25: number;
  source: CalibrationSource;
  qcStatus: string;
}

export type CandidateCheck =
  | { ok: true; candidate: CalibrationCandidate }
  | { ok: false; reason: string };

export function calibrationCandidate(e: ExperimentSummary): CandidateCheck {
  if (e.status !== 'stopped') {
    return { ok: false, reason: e.status === 'running' ? '进行中' : '未正常停止' };
  }
  const c = e.concentration_mmol_l;
  if (c == null || !Number.isFinite(c)) return { ok: false, reason: '未填浓度' };
  const qc = e.qc_status;
  if (qc !== 'PASS' && qc !== 'WARN') {
    return { ok: false, reason: qc === 'FAIL' ? 'QC FAIL' : 'QC 未判定' };
  }
  const useRepresentative = qc === 'PASS' && e.representative_value != null;
  const y = useRepresentative ? e.representative_value : e.k25_median;
  if (y == null || !Number.isFinite(y)) return { ok: false, reason: '无 κ25 代表值' };
  return {
    ok: true,
    candidate: {
      id: e.id,
      experimentUid: e.experiment_id,
      sampleId: e.sample_id ?? '--',
      concentration: c,
      kappa25: y,
      source: useRepresentative ? 'representative' : 'median',
      qcStatus: qc,
    },
  };
}

/** 拟合面板的输入：浓度轴只用 (c, κ25)，t/tc 对标定点无意义，给 NaN 而不是编造数值 */
export function calibrationDataPoints(candidates: CalibrationCandidate[]): DataPoint[] {
  return candidates.map((c) => ({
    t: Number.NaN,
    tc: Number.NaN,
    ec: c.kappa25,
    concentration: c.concentration,
  }));
}

/** 已选实验的签名（与勾选顺序无关）：作为拟合面板 key，换一组点即清掉旧结果 */
export function selectionKey(ids: Iterable<number>): string {
  return [...ids].sort((a, b) => a - b).join(',');
}
