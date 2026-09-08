import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildFitPoints } from './fitPoints.ts';
import type { DataPoint } from '../types/protocol.ts';

const base = { t: 1, tc: 25, ec: 100 };

test('concentration axis keeps 0 (blank sample) as a valid calibration point', () => {
  const points: DataPoint[] = [
    { ...base, concentration: 0 },
    { ...base, concentration: 5 },
    { ...base, concentration: 10 },
  ];
  const fit = buildFitPoints(points, 'concentration');
  assert.deepEqual(fit, [
    [0, 100],
    [5, 100],
    [10, 100],
  ]);
});

test('concentration axis excludes points without concentration instead of index placeholders', () => {
  const points: DataPoint[] = [
    { ...base, concentration: 2 },
    { ...base }, // 无浓度：不得以 1/2/3… 序号占位混入
    { ...base, concentration: 8 },
    { ...base, concentration: 4 },
  ];
  const fit = buildFitPoints(points, 'concentration');
  assert.deepEqual(fit, [
    [2, 100],
    [8, 100],
    [4, 100],
  ]);
});

test('time and temperature axes map t / tc regardless of concentration', () => {
  const points: DataPoint[] = [
    { ...base, t: 3, tc: 21, concentration: 7 },
    { ...base, t: 4, tc: 22 },
  ];
  assert.deepEqual(buildFitPoints(points, 'time'), [
    [3, 100],
    [4, 100],
  ]);
  assert.deepEqual(buildFitPoints(points, 'temperature'), [
    [21, 100],
    [22, 100],
  ]);
});

test('non-finite ec / t / tc points are dropped on every axis', () => {
  const points: DataPoint[] = [
    { ...base, ec: null },
    { ...base, t: Number.NaN },
    { ...base, tc: Number.POSITIVE_INFINITY },
    { ...base, ec: Number.NaN },
    { ...base },
  ];
  assert.equal(buildFitPoints(points, 'time').length, 1);
  assert.equal(buildFitPoints(points, 'temperature').length, 1);
  assert.equal(buildFitPoints(points, 'concentration').length, 0); // 唯一可用点无浓度
});
