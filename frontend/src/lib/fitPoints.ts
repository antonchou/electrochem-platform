import type { DataPoint, FitAxis } from '../types/protocol';

/** 按 X 轴语义构造拟合输入 (x, y)。
 *
 * 浓度轴只接受带真实浓度的点：0（空白样）是合法标定点必须保留，
 * 无浓度的点不得用序号占位混入——那会让拟合输入直接错误。
 */
export function buildFitPoints(points: DataPoint[], axis: FitAxis): [number, number][] {
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
