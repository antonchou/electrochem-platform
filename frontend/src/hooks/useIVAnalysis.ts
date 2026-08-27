import { useMemo } from 'react';
import type { DataPoint } from '../types/protocol';
import { analyzeIV, type IVAnalysis } from '../lib/ivAnalysis';

/**
 * 从实时缓冲计算 I–V 摘要。count 变化时重算；OLS 为 O(n) 纯循环，10Hz 可接受。
 */
export function useIVAnalysis(
  pointsRef: React.MutableRefObject<DataPoint[]>,
  count: number,
): IVAnalysis {
  return useMemo(() => analyzeIV(pointsRef.current), [pointsRef, count]);
}
