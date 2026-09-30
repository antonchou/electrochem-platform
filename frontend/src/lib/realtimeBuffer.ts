import type { DataPoint } from '../types/protocol';

/**
 * 实时数据缓冲（纯逻辑，由 useRealtimeData 包装；可直接用 node:test 测）。
 *
 * - points：定长 FIFO（上限 maxPoints）。追加是原地 push/splice；清空与水合会整体
 *   替换数组，图表据引用变化重建坐标轴。
 * - revision：内容每变一次 +1（含封顶后的追加）。派生计算要依赖它而不是点数——
 *   封顶后点数恒为上限，按点数判断会让 I–V 分析停在封顶那一刻（09-30 审查 #2）。
 * - experimentId：缓冲所属实验。帧 / 状态帧 / 当前实验接口带来的 id 与归属不同，
 *   先清空再追加，避免后端重启、旁观端、断线重连后新旧实验混算（09-30 审查 #3）。
 * - generation：归属每变一次 +1，供异步请求判断其结果是否已被更新的事件取代。
 */
export class RealtimeBuffer {
  points: DataPoint[] = [];
  experimentId: number | null = null;
  runStartT: number | null = null;
  revision = 0;
  generation = 0;
  readonly maxPoints: number;

  constructor(maxPoints: number) {
    this.maxPoints = maxPoints;
  }

  /** 对齐所属实验：id 与当前归属不同则先清空。返回是否发生了清空。 */
  bindExperiment(id: number | null | undefined): boolean {
    if (id == null || id === this.experimentId) return false;
    const switched = this.experimentId !== null;
    if (switched) this.replacePoints([]);
    this.experimentId = id;
    this.generation += 1;
    return switched;
  }

  /** 追加一帧；帧带所属实验时先对齐归属（调试 burst / 浏览器模拟帧不带 id，直接追加）。 */
  push(point: DataPoint, experimentId?: number): void {
    this.bindExperiment(experimentId);
    this.points.push(point);
    if (this.points.length > this.maxPoints) {
      this.points.splice(0, this.points.length - this.maxPoints);
    }
    if (this.runStartT === null) this.runStartT = point.t;
    this.revision += 1;
  }

  /** 清空并解除归属（复位到 idle、手动清空）。 */
  clear(): void {
    this.replacePoints([]);
    this.experimentId = null;
    this.generation += 1;
  }

  /**
   * 续跑水合：库里的历史帧 + 内存中晚于历史末帧的实时帧，钳到上限。
   * 去重基准取最后一个有限 t（P2-3）：t 为 NaN 的历史帧排在末尾时，拿 NaN 比较会让
   * p.t > lastT 恒为 false，把内存中的实时帧整段丢掉；一个有限 t 都没有时全保留。
   */
  hydrate(history: DataPoint[], experimentId?: number): void {
    this.bindExperiment(experimentId);
    let lastT = Number.NEGATIVE_INFINITY;
    for (let i = history.length - 1; i >= 0; i--) {
      if (Number.isFinite(history[i].t)) {
        lastT = history[i].t;
        break;
      }
    }
    const merged = history.concat(this.points.filter((p) => p.t > lastT));
    this.replacePoints(
      merged.length > this.maxPoints ? merged.slice(merged.length - this.maxPoints) : merged,
    );
  }

  private replacePoints(next: DataPoint[]): void {
    this.points = next;
    this.runStartT = next.length > 0 ? next[0].t : null;
    this.revision += 1;
  }
}
