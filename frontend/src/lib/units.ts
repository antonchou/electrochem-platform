/** 按数量级选择电导率单位：μS/cm 或 mS/cm。 */
export function formatConductivityUsCm(usCm: number, digits?: number): { value: number; unit: 'μS/cm' | 'mS/cm'; text: string } {
  if (!Number.isFinite(usCm)) {
    return { value: usCm, unit: 'μS/cm', text: '--' };
  }
  if (Math.abs(usCm) >= 1000) {
    const value = usCm / 1000;
    const d = digits ?? 3;
    return { value, unit: 'mS/cm', text: `${value.toFixed(d)} mS/cm` };
  }
  const d = digits ?? 1;
  return { value: usCm, unit: 'μS/cm', text: `${usCm.toFixed(d)} μS/cm` };
}

/** 电导 G：S → μS 或 mS。 */
export function formatConductanceS(siemens: number, digits?: number): { value: number; unit: 'μS' | 'mS'; text: string } {
  if (!Number.isFinite(siemens)) {
    return { value: siemens, unit: 'μS', text: '--' };
  }
  const uS = siemens * 1e6;
  if (Math.abs(uS) >= 1000) {
    const value = uS / 1000;
    const d = digits ?? 3;
    return { value, unit: 'mS', text: `${value.toFixed(d)} mS` };
  }
  const d = digits ?? 3;
  return { value: uS, unit: 'μS', text: `${uS.toFixed(d)} μS` };
}

/** 电流：A → μA 或 mA。 */
export function formatCurrentA(amperes: number): { value: number; unit: 'μA' | 'mA'; scale: number } {
  if (Math.abs(amperes) >= 1e-3) {
    return { value: amperes * 1e3, unit: 'mA', scale: 1e3 };
  }
  return { value: amperes * 1e6, unit: 'μA', scale: 1e6 };
}

export function formatOhms(ohm: number): string {
  if (!Number.isFinite(ohm)) return '--';
  if (Math.abs(ohm) >= 1e6) return `${(ohm / 1e6).toFixed(3)} MΩ`;
  if (Math.abs(ohm) >= 1e3) return `${(ohm / 1e3).toFixed(2)} kΩ`;
  return `${ohm.toFixed(1)} Ω`;
}

export function strideSample<T>(items: T[], max: number): T[] {
  if (max <= 0 || items.length <= max) return items;
  const step = items.length / max;
  const out: T[] = [];
  for (let i = 0; i < max; i += 1) {
    out.push(items[Math.floor(i * step)]);
  }
  return out;
}
