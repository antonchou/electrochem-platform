import type {
  Calibration,
  ConcentrationAnalysis,
  LabState,
  Measurement,
  Point,
  Standard,
  TemperatureFit,
} from './types.ts';

export class ApiError extends Error {}

/** 后端的错误体：{"detail": "中文原因"} 或校验错误 {"detail": [{loc, msg}]}。 */
function describe(detail: unknown, status: number): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e: { loc?: unknown[]; msg?: string }) => `${(e.loc ?? []).slice(1).join('.') || '请求'}：${e.msg ?? ''}`)
      .join('；');
  }
  return `请求失败（HTTP ${status}）`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers: { 'content-type': 'application/json', ...init?.headers } });
  } catch {
    throw new ApiError('连不上后端');
  }
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = ((await response.json()) as { detail?: unknown }).detail;
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new ApiError(describe(detail, response.status));
  }
  return (await response.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });

export interface StartRequest {
  sample_name: string;
  concentration_mmol_l: number | null;
  note: string | null;
}

export interface CalibrationRequest {
  points: { measurement_id: number; standard_name: string; standard_kappa25_us_cm: number }[];
  operator?: string | null;
  cell_id?: string | null;
  lot?: string | null;
  note?: string | null;
}

export const api = {
  state: () => request<LabState>('/api/state'),
  standards: () => request<Standard[]>('/api/standards'),
  start: (body: StartRequest) => post<Measurement>('/api/measurements', body),
  stop: () => post<Measurement>('/api/measurements/current/stop'),
  measurements: () => request<Measurement[]>('/api/measurements'),
  measurement: (id: number) => request<Measurement>(`/api/measurements/${id}`),
  points: (id: number, maxPoints = 4000) =>
    request<{ total: number; step: number; points: Point[] }>(`/api/measurements/${id}/points?max_points=${maxPoints}`),
  temperatureFit: (id: number) => request<TemperatureFit>(`/api/measurements/${id}/temperature-fit`),
  concentration: (ids: number[]) => post<ConcentrationAnalysis>('/api/analysis/concentration', { measurement_ids: ids }),
  calibrations: () => request<Calibration[]>('/api/calibrations'),
  calibrate: (body: CalibrationRequest) => post<Calibration & { fit: unknown }>('/api/calibrations', body),
  csvUrl: (id: number) => `/api/measurements/${id}/frames.csv`,
};

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
