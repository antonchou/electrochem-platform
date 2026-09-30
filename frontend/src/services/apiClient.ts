import type {
  CalibrationResponse,
  ControlAction,
  ControlResponse,
  CurrentExperiment,
  ExperimentDetail,
  ExperimentStartOptions,
  ExperimentSummary,
  FitAxis,
  FitResponse,
  FramesMode,
  RawFrame,
} from '../types/protocol';

export type ExportFormat = 'csv' | 'json';

/** JSON POST 请求体（body 缺省时不带请求体） */
function jsonPost(body?: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

/**
 * REST 客户端。控制指令走 HTTP，实时数据走 WebSocket。
 * Phase 7 新增：历史实验查询与导出。
 * 若后端地址变化，只需改 config.server.apiBase，本模块无需改动。
 */
export class ApiClient {
  constructor(private readonly baseUrl: string) {}

  /** 请求一个 JSON 接口；非 2xx 抛出「<what>：HTTP <状态码>」 */
  private async request<T>(path: string, what: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, init);
    if (!res.ok) throw new Error(`${what}：HTTP ${res.status}`);
    return (await res.json()) as T;
  }

  control(action: ControlAction, body?: ExperimentStartOptions): Promise<ControlResponse> {
    return this.request(`/api/experiment/${action}`, '控制请求失败', jsonPost(body));
  }

  /** 当前内存态实验（刷新/重连后恢复导出与样品号） */
  getCurrentExperiment(): Promise<CurrentExperiment> {
    return this.request('/api/experiment/current', '当前实验获取失败');
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
  listExperiments(): Promise<ExperimentSummary[]> {
    return this.request('/api/experiments', '历史实验列表获取失败');
  }

  /** 实验详情（含样品汇总） */
  getExperiment(id: number): Promise<ExperimentDetail> {
    return this.request(`/api/experiments/${id}`, '实验详情获取失败');
  }

  /**
   * 原始帧。mode：head = 从头取 limit 条；tail = 最新 limit 条（续跑水合）；
   * even = 全实验等间隔抽样至多 limit 条、保留首末帧（历史详情曲线/拟合）。
   */
  async getFrames(id: number, limit = 3000, mode: FramesMode = 'head'): Promise<RawFrame[]> {
    const body = await this.request<{ frames: RawFrame[] }>(
      `/api/experiments/${id}/frames?limit=${limit}&mode=${mode}`,
      '帧数据获取失败',
    );
    return body.frames;
  }

  /** 备选公式拟合：传入数据点、模型与 X 轴语义，返回按 R² 排序的结果与拟合曲线 */
  fitPoints(
    points: [number, number][],
    models: string[],
    xAxis: FitAxis = 'time',
    experimentId?: number | null,
  ): Promise<FitResponse> {
    return this.request(
      '/api/analysis/fit',
      '拟合请求失败',
      jsonPost({
        x: points.map((p) => p[0]),
        y: points.map((p) => p[1]),
        models,
        x_axis: xAxis,
        experiment_id: experimentId ?? undefined,
      }),
    );
  }

  /** 跨实验浓度标定：服务端按实验取点（一实验一点）并按浓度轴模型拟合，报告写 data/derived */
  async fitCalibration(experimentIds: number[], models: string[]): Promise<CalibrationResponse> {
    const res = await fetch(
      `${this.baseUrl}/api/analysis/calibration`,
      jsonPost({ experiment_ids: experimentIds, models }),
    );
    if (!res.ok) {
      // 400 带具体原因（哪个实验不合格），比 HTTP 状态码可读
      let detail = '';
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body.detail === 'string') detail = body.detail;
      } catch {
        /* 非 JSON 响应：退回状态码提示 */
      }
      throw new Error(detail ? `标定失败：${detail}` : `标定请求失败：HTTP ${res.status}`);
    }
    return (await res.json()) as CalibrationResponse;
  }

  /** CSV / JSON 导出下载地址 */
  exportUrl(id: number, format: ExportFormat): string {
    return `${this.baseUrl}/api/experiments/${id}/export.${format}`;
  }
}
