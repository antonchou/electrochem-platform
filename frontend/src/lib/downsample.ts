/**
 * 等间隔降采样到 ≤ max 个元素，保留首末元素；不超过上限时原样返回（同一引用）。
 * 下标取 floor(i·(n−1)/(max−1))，与后端 storage.get_frames_even 同口径。
 * 拟合输入封顶与图表显示抽样共用这一个实现。
 */
export function downsample<T>(items: T[], max: number): T[] {
  if (max <= 0 || items.length <= max) return items;
  const out: T[] = new Array(max);
  for (let i = 0; i < max - 1; i++) {
    out[i] = items[Math.floor((i * (items.length - 1)) / (max - 1))];
  }
  out[max - 1] = items[items.length - 1];
  return out;
}
