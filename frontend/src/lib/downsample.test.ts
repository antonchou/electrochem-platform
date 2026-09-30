import assert from 'node:assert/strict';
import { test } from 'node:test';
import { downsample } from './downsample.ts';

test('downsample caps length keeping first and last in order (T-02)', () => {
  const total = 20_500;
  const pairs: [number, number][] = Array.from({ length: total }, (_, i) => [i, i * 2]);
  const capped = downsample(pairs, 20_000);
  assert.equal(capped.length, 20_000);
  assert.deepEqual(capped[0], [0, 0]);
  assert.deepEqual(capped[capped.length - 1], [total - 1, (total - 1) * 2]);
  // 单调不减（等间隔抽样不得回绕乱序）
  for (let i = 1; i < capped.length; i++) {
    assert.ok(capped[i][0] >= capped[i - 1][0]);
  }
});

test('downsample picks evenly spaced items and returns small inputs as-is', () => {
  const src = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
  assert.deepEqual(downsample(src, 5), [0, 2, 4, 6, 9]);
  // 未超限时原样返回（同一引用语义）
  assert.equal(downsample(src, 20), src);
});
