import assert from 'node:assert/strict';
import { test } from 'node:test';
import { DASH, duration, fixed, flagLabel, kappa, kappaText, percent, scaled, sig } from './format.ts';

test('missing values render as a dash', () => {
  assert.equal(fixed(null, 2), DASH);
  assert.equal(fixed(Number.NaN, 2), DASH);
  assert.equal(sig(undefined), DASH);
  assert.equal(kappaText(null), DASH);
  assert.equal(percent(Number.POSITIVE_INFINITY), DASH);
});

test('significant digits', () => {
  assert.equal(sig(1413.24), '1413');
  assert.equal(sig(147.04), '147.0');
  assert.equal(sig(1.5), '1.500');
  assert.equal(sig(0.0012346), '0.001235');
  assert.equal(sig(0), '0');
});

test('SI prefixes', () => {
  assert.deepEqual(scaled(1.2345e-4, 'A'), { text: '123.5', unit: 'µA' });
  assert.deepEqual(scaled(0.0842, 'V'), { text: '84.20', unit: 'mV' });
  assert.deepEqual(scaled(0, 'A'), { text: '0', unit: 'A' });
  assert.deepEqual(scaled(2.5e-10, 'A'), { text: '0.2500', unit: 'nA' });
});

test('conductivity switches to mS/cm above 10 000 µS/cm', () => {
  assert.deepEqual(kappa(1413.2), { text: '1413', unit: 'µS/cm' });
  assert.deepEqual(kappa(12880), { text: '12.88', unit: 'mS/cm' });
});

test('durations', () => {
  assert.equal(duration(12.34), '12.3 s');
  assert.equal(duration(125), '2:05');
});

test('flag labels fall back to the raw flag', () => {
  assert.equal(flagLabel('OPEN_CIRCUIT'), '开路：电极没浸入溶液？');
  assert.equal(flagLabel('NEW_FLAG'), 'NEW_FLAG');
});
