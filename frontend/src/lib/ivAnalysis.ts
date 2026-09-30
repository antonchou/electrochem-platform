import type { DataPoint, RawFrame } from '../types/protocol';

/** 电压扫描跨度低于该值时，不把 OLS 斜率当成 I–V 电导。 */
export const MIN_VOLTAGE_SPAN_V = 0.05;
export const MIN_IV_POINTS = 3;
export const MIN_LINEAR_R2 = 0.95;

export type IVFitReason =
  | 'no_data'
  | 'insufficient_samples'
  | 'voltage_span_too_small'
  | 'nonlinear'
  | 'undetermined'
  | 'ok';

export interface IVAnalysis {
  n: number;
  vMin: number | null;
  vMax: number | null;
  vSpan: number | null;
  iMin: number | null;
  iMax: number | null;
  /** OLS 斜率 G，单位 S。跨度不足时仍计算，但不作为结论。 */
  slopeS: number | null;
  interceptA: number | null;
  r2: number | null;
  /** 各点 I/U（U>0）或后端 conductance_s 的平均 */
  meanG: number | null;
  meanKappa25: number | null;
  meanKappaT: number | null;
  meanTemperature: number | null;
  cellConstantPerCm: number | null;
  linearOk: boolean;
  reason: IVFitReason;
  /** 结果卡用电导：线性成立用斜率，否则用平均 I/U */
  conductanceS: number | null;
  resistanceOhm: number | null;
  kappa25: number | null;
  /** 拟合直线端点 [V, I]，仅 linearOk 时有值 */
  fitLine: [number, number][] | null;
}

const EMPTY: IVAnalysis = {
  n: 0,
  vMin: null,
  vMax: null,
  vSpan: null,
  iMin: null,
  iMax: null,
  slopeS: null,
  interceptA: null,
  r2: null,
  meanG: null,
  meanKappa25: null,
  meanKappaT: null,
  meanTemperature: null,
  cellConstantPerCm: null,
  linearOk: false,
  reason: 'no_data',
  conductanceS: null,
  resistanceOhm: null,
  kappa25: null,
  fitLine: null,
};

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function rawFrameToPoint(frame: RawFrame): DataPoint {
  return {
    // t_seconds 缺失给 NaN（而非 0）让下游 buildFitPoints 过滤，避免 (0, ec) 假点（T-04）
    t: frame.t_seconds ?? Number.NaN,
    ec: frame.kappa_25_us_cm ?? frame.k25 ?? frame.ec_raw,
    tc: frame.temperature_raw,
    voltage_raw_v: frame.voltage_raw_v ?? undefined,
    current_raw_a: frame.current_raw_a ?? undefined,
    conductance_s: frame.conductance_s ?? undefined,
    kappa_t_us_cm: frame.kappa_t_us_cm ?? undefined,
    kappa_25_us_cm: frame.kappa_25_us_cm ?? undefined,
    quality_flags: frame.quality_flags ?? undefined,
    excitation_frequency_hz: frame.excitation_frequency_hz ?? undefined,
    excitation_amplitude_v: frame.excitation_amplitude_v ?? undefined,
    calibration_id: frame.calibration_id ?? undefined,
  };
}

export function analyzeIV(points: readonly DataPoint[]): IVAnalysis {
  // 每个 revision（10 Hz）对最多 2 万点重算：直接累加，不为每个点建临时对象
  let n = 0;
  let vMin = Infinity;
  let vMax = -Infinity;
  let iMin = Infinity;
  let iMax = -Infinity;
  let sumG = 0;
  let nG = 0;
  let sumK25 = 0;
  let nK25 = 0;
  let sumKT = 0;
  let nKT = 0;
  let sumT = 0;
  let nT = 0;
  let sumV = 0;
  let sumI = 0;
  let sumVV = 0;
  let sumVI = 0;

  for (const p of points) {
    const v = p.voltage_raw_v;
    const i = p.current_raw_a;
    if (!isFiniteNumber(v) || !isFiniteNumber(i)) continue;
    n += 1;
    vMin = Math.min(vMin, v);
    vMax = Math.max(vMax, v);
    iMin = Math.min(iMin, i);
    iMax = Math.max(iMax, i);
    // G 优先取后端 conductance_s，否则取 I/U（U>0）
    const g = isFiniteNumber(p.conductance_s) ? p.conductance_s : v > 0 ? i / v : null;
    if (g != null) {
      sumG += g;
      nG += 1;
    }
    const k25 = isFiniteNumber(p.kappa_25_us_cm) ? p.kappa_25_us_cm : isFiniteNumber(p.ec) ? p.ec : null;
    if (k25 != null) {
      sumK25 += k25;
      nK25 += 1;
    }
    if (isFiniteNumber(p.kappa_t_us_cm)) {
      sumKT += p.kappa_t_us_cm;
      nKT += 1;
    }
    if (isFiniteNumber(p.tc)) {
      sumT += p.tc;
      nT += 1;
    }
    sumV += v;
    sumI += i;
    sumVV += v * v;
    sumVI += v * i;
  }

  if (n === 0) return { ...EMPTY };
  if (n < MIN_IV_POINTS) {
    return { ...EMPTY, n, reason: 'insufficient_samples' };
  }

  const vSpan = vMax - vMin;
  const meanG = nG > 0 ? sumG / nG : null;
  const meanKappa25 = nK25 > 0 ? sumK25 / nK25 : null;
  const meanKappaT = nKT > 0 ? sumKT / nKT : null;
  const meanTemperature = nT > 0 ? sumT / nT : null;
  const cellConstantPerCm =
    meanKappaT != null && meanG != null && Math.abs(meanG) > 1e-18
      ? meanKappaT / (meanG * 1e6)
      : null;

  let slopeS: number | null = null;
  let interceptA: number | null = null;
  let r2: number | null = null;
  const denom = n * sumVV - sumV * sumV;
  if (Math.abs(denom) > 1e-18) {
    const slope = (n * sumVI - sumV * sumI) / denom;
    const intercept = (sumI - slope * sumV) / n;
    const meanI = sumI / n;
    let ssTot = 0;
    let ssRes = 0;
    for (const p of points) {
      const v = p.voltage_raw_v;
      const i = p.current_raw_a;
      if (!isFiniteNumber(v) || !isFiniteNumber(i)) continue;
      const err = i - (slope * v + intercept);
      ssRes += err * err;
      const dI = i - meanI;
      ssTot += dI * dI;
    }
    slopeS = slope;
    interceptA = intercept;
    // 退化判据（P1-5）：电流恒定时线性度不可判定 → r2 置 null（T-13）。
    // 阈值用相对口径（与后端 analysis._pack 一致）：ssTot ≤ 1e-12·mean²·n，
    // 即相对波动 < 1e-6 视为恒定；绝对阈值会误杀小量级真实信号（电流 A ~1e-6）。
    r2 = ssTot <= 1e-12 * meanI * meanI * n ? null : 1 - ssRes / ssTot;
    // 只拒绝非有限数值；r2 为 null（不可判定）不得并入该分支——
    // Number.isFinite(null) === false，混在一起会把有效的 slopeS/interceptA 一并清掉（P1-3）
    if (
      !Number.isFinite(slopeS) ||
      !Number.isFinite(interceptA) ||
      (r2 !== null && !Number.isFinite(r2))
    ) {
      slopeS = null;
      interceptA = null;
      r2 = null;
    }
  }

  let reason: IVFitReason = 'ok';
  let linearOk = false;
  if (vSpan < MIN_VOLTAGE_SPAN_V) {
    reason = 'voltage_span_too_small';
  } else if (r2 == null) {
    // 电流恒定（ssTot≈0）时线性度不可判定（T-13）：不得把 r2 硬编码 1 当成
    // “完美拟合”得出 G≈0 的假结论。电导回退用各点 I/U 的平均。
    reason = 'undetermined';
  } else if (r2 < MIN_LINEAR_R2) {
    reason = 'nonlinear';
  } else {
    linearOk = true;
  }

  const conductanceS = linearOk ? slopeS : meanG;
  const resistanceOhm =
    conductanceS != null && Math.abs(conductanceS) > 1e-18 ? 1 / conductanceS : null;

  let fitLine: [number, number][] | null = null;
  if (linearOk && slopeS != null && interceptA != null) {
    const hi = vMax === vMin ? vMin + 1e-6 : vMax;
    fitLine = [
      [vMin, slopeS * vMin + interceptA],
      [hi, slopeS * hi + interceptA],
    ];
  }

  return {
    n,
    vMin,
    vMax,
    vSpan,
    iMin,
    iMax,
    slopeS,
    interceptA,
    r2,
    meanG,
    meanKappa25,
    meanKappaT,
    meanTemperature,
    cellConstantPerCm,
    linearOk,
    reason,
    conductanceS,
    resistanceOhm,
    kappa25: meanKappa25,
    fitLine,
  };
}

export function ivReasonMessage(reason: IVFitReason): string {
  switch (reason) {
    case 'no_data':
      return '还没有电压/电流数据。开始实验后，点会随测量出现在图上。';
    case 'insufficient_samples':
      return '点数不足，暂不能拟合 I–V。';
    case 'voltage_span_too_small':
      return '当前激励电压几乎不变，不是电压扫描，不能用 I–V 斜率当电导。电导改用各点 I/U 的平均；κ 来自后端计算链。';
    case 'nonlinear':
      return 'I–V 明显不是直线，不强制给出线性电导。请检查电极、激励或溶液是否稳定。';
    case 'undetermined':
      return '电流几乎不变，线性度无法判定；电导改用各点 I/U 的平均，κ 来自后端计算链。';
    case 'ok':
      return 'I–V 近似直线，电导 G 取拟合斜率。';
  }
}

export function formatIVEquation(slopeS: number, interceptA: number): string {
  const g = slopeS * 1e3; // S → mS
  const b = interceptA * 1e3; // A → mA
  const gAbs = Math.abs(g);
  const bAbs = Math.abs(b);
  const gText = `${g.toFixed(gAbs >= 0.1 ? 2 : 3)} mS`;
  const sign = interceptA >= 0 ? '+' : '−';
  const bText = `${bAbs.toFixed(bAbs >= 0.1 ? 2 : 3)} mA`;
  return `I = ${gText} × V ${sign} ${bText}`;
}
