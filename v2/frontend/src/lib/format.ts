// 数值、单位与中文标签。纯函数，便于单测。

export const DASH = '—';

export function isNum(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function fixed(value: number | null | undefined, digits: number): string {
  return isNum(value) ? value.toFixed(digits) : DASH;
}

/** 有效数字 n 位（保留整数部分），用于量级跨度大的值。 */
export function sig(value: number | null | undefined, digits = 4): string {
  if (!isNum(value)) return DASH;
  if (value === 0) return '0';
  const magnitude = Math.floor(Math.log10(Math.abs(value)));
  return value.toFixed(Math.max(0, digits - 1 - magnitude));
}

/** 按量级选前缀：1.23e-4 A → { text: '123.0', unit: 'µA' }。 */
export function scaled(value: number | null | undefined, unit: string, digits = 4): { text: string; unit: string } {
  if (!isNum(value)) return { text: DASH, unit };
  if (value === 0) return { text: '0', unit };
  const abs = Math.abs(value);
  const prefixes: [number, string][] = [
    [1, ''],
    [1e-3, 'm'],
    [1e-6, 'µ'],
    [1e-9, 'n'],
  ];
  for (const [factor, prefix] of prefixes) {
    if (abs >= factor || factor === 1e-9) return { text: sig(value / factor, digits), unit: prefix + unit };
  }
  return { text: sig(value, digits), unit };
}

/** 电导率：< 10 000 µS/cm 用 µS/cm，否则用 mS/cm。 */
export function kappa(value: number | null | undefined): { text: string; unit: string } {
  if (!isNum(value)) return { text: DASH, unit: 'µS/cm' };
  if (Math.abs(value) >= 10_000) return { text: sig(value / 1000, 4), unit: 'mS/cm' };
  return { text: sig(value, 4), unit: 'µS/cm' };
}

export function kappaText(value: number | null | undefined): string {
  const { text, unit } = kappa(value);
  return text === DASH ? DASH : `${text} ${unit}`;
}

export function percent(value: number | null | undefined, digits = 2): string {
  return isNum(value) ? `${(value * 100).toFixed(digits)}%` : DASH;
}

export function duration(seconds: number | null | undefined): string {
  if (!isNum(seconds)) return DASH;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export const FLAG_LABELS: Record<string, string> = {
  SATURATED: 'ADC 饱和',
  OPEN_CIRCUIT: '开路：电极没浸入溶液？',
  SHORT_CIRCUIT: '短路或超出量程',
  DROPOUT: '采样失败',
  POLARITY: '极性反了：检查接线',
  WAVEFORM_UNSTABLE: '波形不稳',
  TEMP_INVALID: '温度无效',
  SEQ_GAP: '丢帧',
  DEVICE_RESTART: '设备重启',
};

export const HARD_FLAGS = new Set(['SATURATED', 'OPEN_CIRCUIT', 'SHORT_CIRCUIT', 'DROPOUT', 'POLARITY']);

export function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? flag;
}

export const VERDICT_LABELS: Record<string, string> = { PASS: '稳定', WARN: '基本稳定', FAIL: '不稳定' };

export const REASON_LABELS: Record<string, string> = {
  no_data: '没有数据',
  hard_flags: '窗口内有硬异常',
  too_many_invalid: '无效帧过多',
  insufficient_points: '有效点太少',
  high_variation: '波动过大',
  drift: '读数明显漂移',
  some_invalid: '含无效帧',
  variation: '波动偏大',
  slight_drift: '读数轻微漂移',
  temperature_unstable: '温度未稳定',
  waveform_unstable: '波形不稳',
  window_incomplete: '测量时长不到一个判稳窗口',
};

export function reasonLabel(reason: string): string {
  return REASON_LABELS[reason] ?? reason;
}

export const STATUS_LABELS: Record<string, string> = { running: '进行中', completed: '已完成', aborted: '中断' };
