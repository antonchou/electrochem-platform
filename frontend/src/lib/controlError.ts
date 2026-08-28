import type { ControlResponse } from '../types/protocol';

/**
 * 控制接口哪些文案进错误横幅。
 * ok 响应上的提示（如幂等 stop）不是错误；落库降级即使 ok 也要展示。
 */
export function errorFromControlResponse(
  res: Pick<ControlResponse, 'ok' | 'message' | 'persistence'>,
): string | null {
  if (!res.ok) return res.message ?? '控制请求失败';
  if (res.persistence === 'degraded') return res.message ?? null;
  return null;
}
