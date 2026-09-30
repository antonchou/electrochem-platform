import assert from 'node:assert/strict';
import { test } from 'node:test';
import { initialLive, MAX_MONITOR_POINTS, parseMessage, reduce, type LiveState } from './live.ts';
import type { LabState, Measurement, Point, ServerMessage } from './types.ts';

const point = (t: number, seq: number | null = null): Point => ({
  seq,
  t_s: t,
  voltage_v: 0.1,
  current_a: 1e-4,
  temperature_c: 25,
  conductance_s: 1e-3,
  kappa_t_us_cm: 1000,
  kappa25_us_cm: 1000,
  flags: [],
});

const measurement = (id: number) => ({ id, sample_name: 'KCl' }) as Measurement;

const lab = (current: Measurement | null): LabState =>
  ({
    device: { kind: 'sim', connected: true, message: null, info: {} },
    measurement: current,
    last_finished: null,
    calibration: null,
    cell_constant_per_cm: 1,
    alpha_per_c: 0.02,
    storage_error: null,
  }) as unknown as LabState;

const send = (state: LiveState, message: ServerMessage) => reduce(state, { kind: 'message', message });

test('snapshot replaces everything', () => {
  const s = send(initialLive, { type: 'snapshot', state: lab(null), points: [point(1), point(2)], qc: null });
  assert.equal(s.connected, true);
  assert.equal(s.points.length, 2);
  assert.equal(s.latest?.t_s, 2);
});

test('monitor readings append; readings of another mode are ignored', () => {
  let s = send(initialLive, { type: 'snapshot', state: lab(null), points: [], qc: null });
  s = send(s, { type: 'reading', measurement_id: null, point: point(1) });
  assert.equal(s.points.length, 1);
  const before = s;
  s = send(s, { type: 'reading', measurement_id: 7, point: point(2, 1) }); // 已结束 / 未开始的测量
  assert.equal(s, before);
});

test('starting a measurement clears the chart and then records only its readings', () => {
  let s = send(initialLive, { type: 'snapshot', state: lab(null), points: [point(1)], qc: null });
  s = send(s, { type: 'state', state: lab(measurement(7)) });
  assert.deepEqual(s.points, []);
  s = send(s, { type: 'reading', measurement_id: null, point: point(2) }); // 监视尾巴
  assert.equal(s.points.length, 0);
  const qc = { verdict: 'PASS' } as never;
  s = send(s, { type: 'reading', measurement_id: 7, point: point(0.5, 1), qc });
  assert.equal(s.points.length, 1);
  assert.equal(s.qc, qc);
  // 同一测量的状态更新（如设备消息）不清曲线
  s = send(s, { type: 'state', state: { ...lab(measurement(7)), storage_error: 'x' } });
  assert.equal(s.points.length, 1);
  // 测量结束：回到监视，曲线与判稳清空
  s = send(s, { type: 'state', state: lab(null) });
  assert.deepEqual(s.points, []);
  assert.equal(s.qc, null);
});

test('monitor buffer is capped', () => {
  let s = send(initialLive, { type: 'snapshot', state: lab(null), points: [], qc: null });
  for (let i = 0; i < MAX_MONITOR_POINTS + 5; i++) {
    s = send(s, { type: 'reading', measurement_id: null, point: point(i) });
  }
  assert.equal(s.points.length, MAX_MONITOR_POINTS);
  assert.equal(s.points[0].t_s, 5);
});

test('any message means connected again', () => {
  let s = send(initialLive, { type: 'snapshot', state: lab(null), points: [], qc: null });
  s = reduce(s, { kind: 'connection', connected: false });
  s = send(s, { type: 'reading', measurement_id: null, point: point(1) });
  assert.equal(s.connected, true);
});

test('connection flag and heartbeat', () => {
  const s = reduce(initialLive, { kind: 'connection', connected: true });
  assert.equal(s.connected, true);
  assert.equal(reduce(s, { kind: 'connection', connected: true }), s);
  assert.equal(send(s, { type: 'heartbeat' }), s);
});

test('parseMessage accepts known messages only', () => {
  assert.deepEqual(parseMessage('{"type":"heartbeat"}'), { type: 'heartbeat' });
  assert.equal(parseMessage('{"type":"unknown"}'), null);
  assert.equal(parseMessage('not json'), null);
  assert.equal(parseMessage('null'), null);
});
