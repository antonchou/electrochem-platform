import assert from 'node:assert/strict';
import { test } from 'node:test';
import { RealtimeBuffer } from './realtimeBuffer.ts';
import type { DataPoint } from '../types/protocol.ts';

const pt = (t: number): DataPoint => ({ t, ec: 1413, tc: 25 });
const ts = (buf: RealtimeBuffer) => buf.points.map((p) => p.t);

test('revision keeps advancing after the buffer is capped (09-30 #2)', () => {
  const buf = new RealtimeBuffer(3);
  for (let i = 0; i < 3; i++) buf.push(pt(i));
  const atCap = buf.revision;
  buf.push(pt(3));
  buf.push(pt(4));
  // 点数封顶不变——旧实现用点数做 memo 依赖，I–V 分析从此冻结
  assert.equal(buf.points.length, 3);
  assert.equal(buf.revision, atCap + 2);
  assert.deepEqual(ts(buf), [2, 3, 4]);
});

test('a frame from another experiment clears the old data first (09-30 #3)', () => {
  const buf = new RealtimeBuffer(100);
  buf.push(pt(0), 7);
  buf.push(pt(0.1), 7);
  const oldArray = buf.points;
  buf.push(pt(0), 8);
  assert.equal(buf.experimentId, 8);
  assert.deepEqual(ts(buf), [0]);
  // 换了新数组：图表据引用变化重建坐标轴
  assert.notEqual(buf.points, oldArray);
});

test('status/current-experiment binding clears only when the id actually changes', () => {
  const buf = new RealtimeBuffer(100);
  buf.push(pt(0), 7);
  assert.equal(buf.bindExperiment(7), false);
  assert.equal(buf.bindExperiment(undefined), false);
  assert.equal(buf.bindExperiment(null), false);
  // 调试 burst / 浏览器模拟帧不带 id：直接追加，不影响归属
  buf.push(pt(0.1));
  assert.deepEqual(ts(buf), [0, 0.1]);
  assert.equal(buf.bindExperiment(9), true);
  assert.equal(buf.points.length, 0);
  assert.equal(buf.experimentId, 9);
});

test('an unbound buffer adopts the first experiment id without clearing', () => {
  const buf = new RealtimeBuffer(100);
  buf.push(pt(0));
  assert.equal(buf.bindExperiment(3), false);
  assert.deepEqual(ts(buf), [0]);
  assert.equal(buf.experimentId, 3);
});

test('clear() unbinds and generation changes whenever ownership changes', () => {
  const buf = new RealtimeBuffer(100);
  const g0 = buf.generation;
  buf.push(pt(0), 7);
  assert.ok(buf.generation > g0);
  const g1 = buf.generation;
  buf.push(pt(0.1), 7);
  assert.equal(buf.generation, g1, '同一实验追加不改变归属代数');
  buf.clear();
  assert.equal(buf.experimentId, null);
  assert.equal(buf.points.length, 0);
  assert.ok(buf.generation > g1);
});

test('reconnect alignment: idle clears only a bound buffer, running rebinds', () => {
  // 后端重启后回到 idle：旧实验的点要清掉
  const bound = new RealtimeBuffer(100);
  bound.push(pt(0), 7);
  assert.equal(bound.alignToCurrent({ status: 'idle' }, bound.generation), true);
  assert.equal(bound.points.length, 0);
  assert.equal(bound.experimentId, null);

  // 未归属的点（调试 burst 帧）不被 idle 清掉
  const unbound = new RealtimeBuffer(100);
  unbound.push(pt(0));
  assert.equal(unbound.alignToCurrent({ status: 'idle' }, unbound.generation), false);
  assert.deepEqual(ts(unbound), [0]);

  // 断线期间换了实验：对齐到新 id 并清空旧点；同一实验续跑不清
  const running = new RealtimeBuffer(100);
  running.push(pt(0), 7);
  assert.equal(running.alignToCurrent({ status: 'running', experiment_id: 7 }, running.generation), false);
  assert.deepEqual(ts(running), [0]);
  assert.equal(running.alignToCurrent({ status: 'running', experiment_id: 8 }, running.generation), true);
  assert.equal(running.points.length, 0);
  assert.equal(running.experimentId, 8);
});

test('reconnect alignment is ignored once frames re-bound the buffer in flight', () => {
  const buf = new RealtimeBuffer(100);
  buf.push(pt(0), 7);
  const requestedAt = buf.generation;
  // 请求在途时新实验的帧先到：归属已由帧对齐，过时的 idle 结果不得清掉新实验的点
  buf.push(pt(0), 8);
  assert.equal(buf.alignToCurrent({ status: 'idle' }, requestedAt), false);
  assert.deepEqual(ts(buf), [0]);
  assert.equal(buf.experimentId, 8);
});

test('hydrate merges history with newer live points, clamps, and keeps the binding', () => {
  const buf = new RealtimeBuffer(4);
  // 续跑后请求历史期间已到达的实时帧
  buf.push(pt(5), 7);
  buf.push(pt(6), 7);
  // 历史末帧 t 为 NaN：去重基准回退到最后一个有限 t=5，t=5 的实时帧去重、t=6 保留
  buf.hydrate([pt(1), pt(2), pt(3), pt(5), pt(Number.NaN)], 7);
  assert.deepEqual(ts(buf), [3, 5, Number.NaN, 6]);
  assert.equal(buf.experimentId, 7);

  // 水合的是另一个实验：旧内存帧不得混入
  buf.hydrate([pt(1)], 8);
  assert.deepEqual(ts(buf), [1]);
  assert.equal(buf.experimentId, 8);
});
