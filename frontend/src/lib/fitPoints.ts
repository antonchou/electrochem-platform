import type { DataPoint, FitAxis } from '../types/protocol';
import { downsample } from './downsample.ts';

/** 后端 /api/analysis/fit 的 x/y 单数组上限（schemas.FitRequest max_length） */
export const MAX_FIT_POINTS = 20_000;

/** 按 X 轴语义筛出可参与拟合的 (x, y)（未截断）。
 *
 * 浓度轴只接受带真实浓度的点：0（空白样）是合法标定点必须保留，
 * 无浓度的点不得用序号占位混入——那会让拟合输入直接错误。
 * 浓度轴只要求 ec 与浓度有限：跨实验标定的点是“一实验一点”，本就没有 t/tc，
 * 不能因无关字段缺失被整点丢弃（09-30 审查 #4）。
 */
export function fitCandidates(points: DataPoint[], axis: FitAxis): [number, number][] {
  const hasEc = (p: DataPoint) => p.ec !== null && Number.isFinite(p.ec);
  if (axis === 'concentration') {
    return points
      .filter((p) => hasEc(p) && p.concentration != null && Number.isFinite(p.concentration))
      .map((p) => [p.concentration as number, p.ec as number] as [number, number]);
  }
  const usable = points.filter((p) => hasEc(p) && Number.isFinite(p.t) && Number.isFinite(p.tc));
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
  return downsample(fitCandidates(points, axis), MAX_FIT_POINTS);
}
