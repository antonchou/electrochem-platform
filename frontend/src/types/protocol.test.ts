import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { ExperimentFrame } from './protocol.ts';
import { parseServerMessage } from './protocol.ts';

/** 断言解析结果是数据帧并收窄联合类型（TestClient 风格的窄化辅助） */
function expectFrame(parsed: ReturnType<typeof parseServerMessage>): ExperimentFrame {
  assert.ok(parsed, 'parseServerMessage 应返回结果');
  assert.ok('ec' in parsed, '应为数据帧而非状态帧');
  return parsed;
}

test('parses a complete mock frame', () => {
  const parsed = expectFrame(
    parseServerMessage({
      timestamp: 1.2,
      ec: 1413.1,
      temperature: 25.0,
      status: 'running',
    }),
  );
  assert.equal(parsed.ec, 1413.1);
});

test('accepts COMPUTE_INVALID frames with null ec', () => {
  const parsed = expectFrame(
    parseServerMessage({
      timestamp: 0.5,
      ec: null,
      temperature: 27.0,
      status: 'running',
      voltage_raw_v: -0.4,
      current_raw_a: 0.001,
      quality_flags: 'CSV|COMPUTE_INVALID',
    }),
  );
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

test('passes through experiment_id on data frames and sample_id on status frames', () => {
  // 09-30 审查 #3：前端实时缓冲按帧所属实验隔离；旁观端据状态帧的样品号更新溶液名
  const frame = expectFrame(
    parseServerMessage({ timestamp: 1, ec: 1413, temperature: 25, status: 'running', experiment_id: 12 }),
  );
  assert.equal(frame.experiment_id, 12);
  // 非整数 id 视为缺失，不得污染缓冲归属
  const odd = expectFrame(
    parseServerMessage({ timestamp: 1, ec: 1413, temperature: 25, status: 'running', experiment_id: '12' }),
  );
  assert.equal(odd.experiment_id, undefined);

  assert.deepEqual(parseServerMessage({ status: 'running', experiment_id: 12, sample_id: 'NACL_010' }), {
    status: 'running',
    experiment_id: 12,
    sample_id: 'NACL_010',
  });
});

test('parses persistence warning on status frames', () => {
  const parsed = parseServerMessage({
    status: 'running',
    experiment_id: 7,
    message: '落库失败：实时曲线仍在更新，但历史和导出将缺帧。请重启后端恢复落库。',
    persistence: 'degraded',
  });
  assert.ok(parsed && !('ec' in parsed));
  assert.equal(parsed.status, 'running');
  assert.equal(parsed.experiment_id, 7);
  assert.equal(parsed.persistence, 'degraded');
  assert.match(parsed.message ?? '', /落库失败/);
  assert.match(parsed.message ?? '', /重启后端/);
});
