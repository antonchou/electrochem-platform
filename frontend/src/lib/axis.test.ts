import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mergeAxisBounds, paddedBounds } from './axis.ts';

test('bounds merged in base units keep data in view across a μA→mA unit flip', () => {
  // μA 阶段：~0.9 mA 电流，轴边界约 850–950 μA（安培域）
  let bounds = mergeAxisBounds(null, paddedBounds(8.5e-4, 9.5e-4, 5e-6));
  // 电流漂过 1 mA，显示单位切到 mA；边界仍按安培合并后必须覆盖新数据
  bounds = mergeAxisBounds(bounds, paddedBounds(1.0e-3, 1.1e-3, 5e-5));
  const dataMiddle = 1.05e-3;
  assert.ok(
    bounds.min < dataMiddle && dataMiddle < bounds.max,
    `merged bounds [${bounds.min}, ${bounds.max}] 应覆盖数据 ${dataMiddle}`,
  );
});

test('merged bounds never shrink', () => {
  const first = mergeAxisBounds(null, paddedBounds(0.2, 1.0, 0.05));
  const second = mergeAxisBounds(first, paddedBounds(0.4, 0.6, 0.05));
  assert.ok(second.min <= first.min && second.max >= first.max);
});

test('padded bounds respect the minimum span in display units', () => {
  // 两个几乎重合的 μA 级电流点：跨度不得小于 minSpan
  const bounds = paddedBounds(10e-6, 10.001e-6, 5e-6);
  assert.ok(bounds.max - bounds.min >= 5e-6);
});

test('relSpan sets the minimum window relative to the reading (EC-t axis uses 1%)', () => {
  assert.deepEqual(paddedBounds(1413, 1413, 10, 0.01), { min: 1405, max: 1425 });
  assert.deepEqual(paddedBounds(1413, 1413, 10), { min: 1395, max: 1430 });
});
