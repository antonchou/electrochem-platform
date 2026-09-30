import type { CurrentExperiment, DataPoint, ExperimentStatus } from '../types/protocol';

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

  /** 追加一帧；帧带所属实验时先对齐归属（调试 burst 帧不带 id，直接追加）。 */
  push(point: DataPoint, experimentId?: number): void {
    this.bindExperiment(experimentId);
    this.points.push(point);
    if (this.points.length > this.maxPoints) {
      this.points.splice(0, this.points.length - this.maxPoints);
    }
    this.revision += 1;
  }

  /**
   * 处理状态帧，返回是否清空了点。
   * - idle：复位，连同未归属的点一起清空。
   * - 其他状态：对齐到帧里的实验 id；别的客户端开了新实验（旁观端）会先清旧点，
   *   续跑同一实验（同 id）绝不清。
   */
  applyStatus(status: ExperimentStatus, experimentId: number | null | undefined): boolean {
    if (status !== 'idle') return this.bindExperiment(experimentId);
    this.clear();
    return true;
  }

  /**
   * 重连后按「当前实验」接口的结果对齐归属，返回是否清空了点。
   * generation 取发请求时的值：请求在途时帧或状态帧已对齐过归属（代数变了），以它们为准。
   * - idle：后端已无实验上下文（重启后旧实验被标 aborted、或别处已复位）。缓冲仍归属某个
   *   旧实验就清空；未归属的点（调试 burst 帧）不动——重连只是对齐、不是复位，这点与
   *   idle 状态帧（applyStatus）不同。
   * - 其他状态：对齐到当前实验 id（断线期间错过的 running 广播不会重发）。
   */
  alignToCurrent(cur: Pick<CurrentExperiment, 'status' | 'experiment_id'>, generation: number): boolean {
    if (generation !== this.generation) return false;
    if (cur.status !== 'idle') return this.bindExperiment(cur.experiment_id);
    if (this.experimentId === null) return false;
    this.clear();
    return true;
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
    this.revision += 1;
  }
}
