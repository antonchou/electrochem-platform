import type {
  ControlAction,
  ControlResponse,
  CurrentExperiment,
  ExperimentDetail,
  ExperimentStartOptions,
  ExperimentSummary,
  FitAxis,
  FitResponse,
  RawFrame,
} from '../types/protocol';

/**
 * REST 客户端。控制指令走 HTTP，实时数据走 WebSocket。
 * Phase 7 新增：历史实验查询与导出。
 * 若后端地址变化，只需改 config.server.apiBase，本模块无需改动。
 */
export class ApiClient {
  constructor(private readonly baseUrl: string) {}

  async control(action: ControlAction, body?: ExperimentStartOptions): Promise<ControlResponse> {
    const res = await fetch(`${this.baseUrl}/api/experiment/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      throw new Error(`控制请求失败：HTTP ${res.status}`);
    }
    return (await res.json()) as ControlResponse;
  }

  /** 当前内存态实验（刷新/重连后恢复导出与样品号） */
  async getCurrentExperiment(): Promise<CurrentExperiment> {
    const res = await fetch(`${this.baseUrl}/api/experiment/current`);
    if (!res.ok) throw new Error(`当前实验获取失败：HTTP ${res.status}`);
    return (await res.json()) as CurrentExperiment;
  }

  /** 跨源也可触发下载：fetch blob，避免 <a download> 整页跳走 */
  async downloadExport(url: string, filename: string): Promise<void> {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`导出失败：HTTP ${res.status}`);
    const blob = await res.blob();
    const href = URL.createObjectURL(blob);
    try {
      const a = document.createElement('a');
      a.href = href;
      a.download = filename;
      a.click();
    } finally {
      URL.revokeObjectURL(href);
    }
  }

  /** 历史实验列表 */
  async listExperiments(): Promise<ExperimentSummary[]> {
    const res = await fetch(`${this.baseUrl}/api/experiments`);
    if (!res.ok) throw new Error(`历史实验列表获取失败：HTTP ${res.status}`);
    return (await res.json()) as ExperimentSummary[];
  }

  /** 实验详情（含样品汇总） */
  async getExperiment(id: number): Promise<ExperimentDetail> {
    const res = await fetch(`${this.baseUrl}/api/experiments/${id}`);
    if (!res.ok) throw new Error(`实验详情获取失败：HTTP ${res.status}`);
    return (await res.json()) as ExperimentDetail;
  }

  /** 原始帧（limit 限制条数，用于静态曲线） */
  async getFrames(id: number, limit = 3000): Promise<RawFrame[]> {
    const res = await fetch(`${this.baseUrl}/api/experiments/${id}/frames?limit=${limit}`);
    if (!res.ok) throw new Error(`帧数据获取失败：HTTP ${res.status}`);
    const body = (await res.json()) as { frames: RawFrame[] };
    return body.frames;
  }

  /** 备选公式拟合：传入数据点、模型与 X 轴语义，返回按 R² 排序的结果与拟合曲线 */
  async fitPoints(
    points: [number, number][],
    models: string[],
    xAxis: FitAxis = 'time',
  ): Promise<FitResponse> {
    const res = await fetch(`${this.baseUrl}/api/analysis/fit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        x: points.map((p) => p[0]),
        y: points.map((p) => p[1]),
        models,
        x_axis: xAxis,
      }),
    });
    if (!res.ok) throw new Error(`拟合请求失败：HTTP ${res.status}`);
    return (await res.json()) as FitResponse;
  }

  /** CSV 导出下载地址 */
  exportCsvUrl(id: number): string {
    return `${this.baseUrl}/api/experiments/${id}/export.csv`;
  }

  /** JSON 导出下载地址 */
  exportJsonUrl(id: number): string {
    return `${this.baseUrl}/api/experiments/${id}/export.json`;
  }
}
