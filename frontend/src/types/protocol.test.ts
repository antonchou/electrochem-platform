import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseServerMessage } from './protocol.ts';

test('parses a complete mock frame', () => {
  const parsed = parseServerMessage({
    timestamp: 1.2,
    ec: 1413.1,
    temperature: 25.0,
    status: 'running',
  });
  assert.ok(parsed && 'ec' in parsed);
  assert.equal(parsed.ec, 1413.1);
});

test('accepts COMPUTE_INVALID frames with null ec', () => {
  const parsed = parseServerMessage({
    timestamp: 0.5,
    ec: null,
    temperature: 27.0,
    status: 'running',
    voltage_raw_v: -0.4,
    current_raw_a: 0.001,
    quality_flags: 'CSV|COMPUTE_INVALID',
  });
  assert.ok(parsed && 'timestamp' in parsed);
  assert.equal(parsed.ec, null);
  assert.equal(parsed.voltage_raw_v, -0.4);
  assert.equal(parsed.quality_flags, 'CSV|COMPUTE_INVALID');
});

test('rejects non-numeric ec strings as illegal frames', () => {
  assert.equal(
    parseServerMessage({ timestamp: 1, ec: 'abc', temperature: 25, status: 'running' }),
    null,
  );
});

test('parses status-only frames', () => {
  const parsed = parseServerMessage({ status: 'stopped' });
  assert.deepEqual(parsed, { status: 'stopped' });
});

test('parses persistence warning on status frames', () => {
  const parsed = parseServerMessage({
    status: 'running',
    experiment_id: 7,
    message: '落库失败：实时曲线仍在更新，但历史和导出将缺帧。',
    persistence: 'degraded',
  });
  assert.ok(parsed && !('ec' in parsed));
  assert.equal(parsed.status, 'running');
  assert.equal(parsed.experiment_id, 7);
  assert.equal(parsed.persistence, 'degraded');
  assert.match(parsed.message ?? '', /落库失败/);
});
