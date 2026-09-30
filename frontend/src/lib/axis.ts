/** 图表坐标轴边界工具。 */

export interface AxisBounds {
  min: number;
  max: number;
}

export function niceStep(span: number): number {
  const roughStep = Math.max(span / 6, Number.EPSILON);
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const normalized = roughStep / magnitude;
  const multiplier = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return multiplier * magnitude;
}

/**
 * 以“nice step”取整，向外扩出一个覆盖数据的轴范围。
 * 跨度取 数据跨度×1.2、|中心值|×relSpan、minSpan 三者最大。
 */
export function paddedBounds(
  dataMin: number,
  dataMax: number,
  minSpan: number,
  relSpan = 0.02,
): AxisBounds {
  const center = (dataMin + dataMax) / 2;
  const span = Math.max((dataMax - dataMin) * 1.2, Math.abs(center) * relSpan, minSpan);
  const step = niceStep(span);
  return {
    min: Math.floor((center - span / 2) / step) * step,
    max: Math.ceil((center + span / 2) / step) * step,
  };
}

/** 只扩不缩地合并边界。prev/next 必须是同一量纲（基础单位）；
 * 跨显示单位（如 μA 与 mA）合并会把轴钉死在错误量级上。 */
export function mergeAxisBounds(prev: AxisBounds | null, next: AxisBounds): AxisBounds {
  if (!prev) return next;
  return {
    min: Math.min(prev.min, next.min),
    max: Math.max(prev.max, next.max),
  };
}
