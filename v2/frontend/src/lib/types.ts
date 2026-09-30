// 与后端 v2/backend/ec 的接口一一对应（字段说明见 v2/docs/接口.md）。

export type Verdict = 'PASS' | 'WARN' | 'FAIL';

export interface Point {
  seq: number | null;
  t_s: number;
  voltage_v: number | null;
  current_a: number | null;
  temperature_c: number | null;
  conductance_s: number | null;
  kappa_t_us_cm: number | null;
  kappa25_us_cm: number | null;
  flags: string[];
}

export interface QcConfig {
  window_s: number;
  min_points: number;
  cv_warn: number;
  cv_fail: number;
  drift_warn: number;
  drift_fail: number;
  temp_span_warn_c: number;
  invalid_fail: number;
  kappa_floor_us_cm: number;
}

export interface Qc {
  verdict: Verdict;
  reasons: string[];
  hard_flags: string[];
  n_points: number;
  n_valid: number;
  window_s: number;
  kappa25_mean: number | null;
  kappa25_sd: number | null;
  cv: number | null;
  drift: number | null;
  temperature_mean: number | null;
  temperature_span: number | null;
  representative_kappa25: number | null;
  config?: QcConfig;
}

export interface DeviceInfo {
  device_id: string | null;
  firmware_version: string | null;
  range_id: string | null;
  excitation_frequency_hz: number | null;
  excitation_amplitude_v: number | null;
}

export interface Measurement extends DeviceInfo {
  id: number;
  sample_name: string;
  concentration_mmol_l: number | null;
  note: string | null;
  status: 'running' | 'completed' | 'aborted';
  started_at: string;
  ended_at?: string | null;
  cell_constant_per_cm: number;
  calibration_id: number | null;
  alpha_per_c: number;
  device_kind: string;
  qc?: Qc | null;
  frame_count?: number;
  duration_s?: number | null;
}

export interface CalibrationPoint {
  measurement_id: number;
  sample_name: string;
  standard_name: string;
  standard_kappa25_us_cm: number;
  conductance25_s: number;
  deviation_pct: number | null;
}

export interface Calibration {
  id: number;
  created_at: string;
  cell_constant_per_cm: number;
  r2: number | null;
  rsd_pct: number | null;
  operator: string | null;
  cell_id: string | null;
  lot: string | null;
  note: string | null;
  points: CalibrationPoint[];
}

export interface LabState {
  device: { kind: string; connected: boolean; message: string | null; info: DeviceInfo };
  measurement: Measurement | null;
  last_finished: Measurement | null;
  calibration: Calibration | null;
  cell_constant_per_cm: number;
  alpha_per_c: number;
  qc_config: QcConfig;
  storage_error: string | null;
}

export type ServerMessage =
  | { type: 'snapshot'; state: LabState; points: Point[]; qc: Qc | null }
  | { type: 'state'; state: LabState }
  | { type: 'reading'; measurement_id: number | null; point: Point; qc?: Qc }
  | { type: 'heartbeat' };

export interface Standard {
  name: string;
  kappa25_us_cm: number;
}

export interface Param {
  value: number;
  se: number | null;
  ci95: [number, number] | null;
}

export type FitResult<T> = ({ ok: true } & T) | { ok: false; error: string };

export interface ConcentrationAnalysis {
  points: { measurement_id: number; sample_name: string; concentration_mmol_l: number; kappa25_us_cm: number }[];
  warnings: string[];
  n: number;
  range_mmol_l: [number, number] | null;
  linear: FitResult<{ intercept_us_cm: Param; slope_us_cm_per_mmol_l: Param; r2: number | null; n: number }>;
  kohlrausch: FitResult<{
    lambda0_s_cm2_per_mol: Param;
    k_s_cm2_per_mol_sqrt_l_per_mol: Param;
    r2: number | null;
    n: number;
  }>;
}

export type TemperatureFit = { measurement_id: number } & FitResult<{
  kappa25_us_cm: Param;
  alpha_per_c: Param;
  r2: number | null;
  n: number;
  temperature_range_c: [number, number];
}>;
