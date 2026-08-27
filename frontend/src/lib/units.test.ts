import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatConductanceS, formatConductivityUsCm, formatCurrentA, formatOhms, strideSample } from './units.ts';

test('conductivity uses μS/cm below 1000 and mS/cm at or above', () => {
  assert.equal(formatConductivityUsCm(141.3).unit, 'μS/cm');
  assert.equal(formatConductivityUsCm(1413).unit, 'mS/cm');
  assert.equal(formatConductivityUsCm(1413).text, '1.413 mS/cm');
});

test('conductance uses μS below 1000 μS and mS otherwise', () => {
  assert.equal(formatConductanceS(1.413e-3).unit, 'mS');
  assert.equal(formatConductanceS(1.413e-3).text, '1.413 mS');
  assert.equal(formatConductanceS(2.5e-6).unit, 'μS');
});

test('current uses mA at or above 1 mA', () => {
  assert.equal(formatCurrentA(1.413e-3).unit, 'mA');
  assert.equal(formatCurrentA(5e-7).unit, 'μA');
});

test('ohms picks kΩ / MΩ', () => {
  assert.equal(formatOhms(708.4), '708.4 Ω');
  assert.equal(formatOhms(15000), '15.00 kΩ');
});

test('strideSample keeps endpoints density without inventing points', () => {
  const src = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
  assert.deepEqual(strideSample(src, 5), [0, 2, 4, 6, 8]);
  assert.equal(strideSample(src, 20), src);
});
