import type { DataPoint, FitAxis } from '../types/protocol';

/** 后端 /api/analysis/fit 的 x/y 单数组上限（schemas.FitRequest max_length） */
export const MAX_FIT_POINTS = 20_000;

/** 按 X 轴语义筛出可参与拟合的 (x, y)（未截断）。
 *
 * 浓度轴只接受带真实浓度的点：0（空白样）是合法标定点必须保留，
 * 无浓度的点不得用序号占位混入——那会让拟合输入直接错误。
 */
export function fitCandidates(points: DataPoint[], axis: FitAxis): [number, number][] {
  const usable = points.filter(
    (p) =>
      p.ec !== null &&
      Number.isFinite(p.ec) &&
      Number.isFinite(p.t) &&
      Number.isFinite(p.tc),
  );
  if (axis === 'concentration') {
    return usable
      .filter((p) => p.concentration != null && Number.isFinite(p.concentration))
      .map((p) => [p.concentration as number, p.ec as number] as [number, number]);
  }
  return usable.map((p) =>
    axis === 'temperature'
      ? ([p.tc, p.ec as number] as [number, number])
      : ([p.t, p.ec as number] as [number, number]),
  );
}

/** 按 X 轴语义构造拟合输入 (x, y)，超过后端上限时等间隔降采样（T-02），
 * 否则长实验（>2 万帧）的拟合请求必然 422。
 */
export function buildFitPoints(points: DataPoint[], axis: FitAxis): [number, number][] {
  return downsample(fitCandidates(points, axis));
}

/** 等间隔降采样到 ≤ max 点，保留首末点；不超过上限时原样返回。 */
export function downsample(
  points: [number, number][],
  max: number = MAX_FIT_POINTS,
): [number, number][] {
  if (points.length <= max) return points;
  const out: [number, number][] = new Array(max);
  for (let i = 0; i < max - 1; i++) {
    out[i] = points[Math.floor((i * (points.length - 1)) / (max - 1))];
  }
  out[max - 1] = points[points.length - 1];
  return out;
}
