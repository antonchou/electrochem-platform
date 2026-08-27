import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { DataPoint } from '../types/protocol.ts';
import {
  analyzeIV,
  formatIVEquation,
  ivReasonMessage,
  MIN_VOLTAGE_SPAN_V,
} from './ivAnalysis.ts';

function pt(partial: Partial<DataPoint> & { t: number }): DataPoint {
  return {
    ec: null,
    tc: 25,
    ...partial,
  };
}

test('linear I-V scan recovers G and high R²', () => {
  const G = 2.31e-3;
  const b = 2e-5;
  const points = [0.2, 0.4, 0.6, 0.8, 1.0].map((v, i) =>
    pt({
      t: i,
      voltage_raw_v: v,
      current_raw_a: G * v + b,
      conductance_s: G,
      kappa_t_us_cm: G * 1e6,
      kappa_25_us_cm: G * 1e6,
      ec: G * 1e6,
    }),
  );
  const result = analyzeIV(points);
  assert.equal(result.linearOk, true);
  assert.equal(result.reason, 'ok');
  assert.ok(result.slopeS != null);
  assert.ok(Math.abs(result.slopeS - G) < 1e-9);
  assert.ok(result.r2 != null && result.r2 > 0.999);
  assert.ok(result.fitLine != null && result.fitLine.length === 2);
  assert.match(formatIVEquation(result.slopeS, result.interceptA ?? 0), /2\.31 mS/);
});

test('almost-constant excitation is not treated as a voltage sweep', () => {
  const G = 1.413e-3;
  const points = Array.from({ length: 20 }, (_, i) => {
    const v = 1.0 + (i % 2 === 0 ? 0.0008 : -0.0008);
    return pt({
      t: i * 0.1,
      voltage_raw_v: v,
      current_raw_a: G * v,
      conductance_s: G,
      kappa_25_us_cm: 1413,
      kappa_t_us_cm: 1413,
      ec: 1413,
    });
  });
  const result = analyzeIV(points);
  assert.equal(result.linearOk, false);
  assert.equal(result.reason, 'voltage_span_too_small');
  assert.ok((result.vSpan ?? 1) < MIN_VOLTAGE_SPAN_V);
  assert.equal(result.fitLine, null);
  assert.ok(result.meanG != null);
  assert.ok(Math.abs((result.conductanceS ?? 0) - G) < 1e-6);
  assert.equal(result.kappa25, 1413);
  assert.match(ivReasonMessage(result.reason), /不是电压扫描/);
});

test('curved I-V is flagged nonlinear instead of forcing G', () => {
  // 明显的“膝点”响应，线性 R² 会掉到阈值以下
  const pairs: [number, number][] = [
    [0.2, 0.10e-3],
    [0.4, 0.12e-3],
    [0.6, 0.15e-3],
    [0.8, 0.80e-3],
    [1.0, 2.50e-3],
  ];
  const points = pairs.map(([v, i], idx) =>
    pt({
      t: idx,
      voltage_raw_v: v,
      current_raw_a: i,
      kappa_25_us_cm: 100,
      ec: 100,
    }),
  );
  const result = analyzeIV(points);
  assert.equal(result.linearOk, false);
  assert.equal(result.reason, 'nonlinear');
  assert.equal(result.fitLine, null);
});

test('missing U/I yields no_data and does not invent results', () => {
  const result = analyzeIV([pt({ t: 1, ec: 1413, tc: 25 })]);
  assert.equal(result.reason, 'no_data');
  assert.equal(result.conductanceS, null);
  assert.equal(result.kappa25, null);
});
