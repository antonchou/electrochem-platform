import { useMemo } from 'react';
import type { DataPoint } from '../types/protocol';
import { analyzeIV, type IVAnalysis } from '../lib/ivAnalysis';

/**
 * 从实时缓冲计算 I–V 摘要。revision（缓冲内容版本号）变化时重算；OLS 为 O(n) 纯循环，10Hz 可接受。
 * 不能用点数做依赖：缓冲封顶后点数恒为上限，结果会停在封顶那一刻（09-30 审查 #2）。
 */
export function useIVAnalysis(
  pointsRef: React.MutableRefObject<DataPoint[]>,
  revision: number,
): IVAnalysis {
  return useMemo(() => analyzeIV(pointsRef.current), [pointsRef, revision]);
}
