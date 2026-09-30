import assert from 'node:assert/strict';
import { test } from 'node:test';
import { calibrationCandidate, calibrationDataPoints, selectionKey } from './calibration.ts';
import { buildFitPoints } from './fitPoints.ts';
import type { ExperimentSummary } from '../types/protocol.ts';

const base: ExperimentSummary = {
  id: 1,
  experiment_id: 'EXP-1',
  title: 't',
  status: 'stopped',
  sample_id: 'NACL_010',
  sensor_path_id: 'MOCK_EC_IV',
  started_at_utc: '2026-09-30T00:00:00Z',
  ended_at_utc: '2026-09-30T00:01:00Z',
  frame_count: 100,
  concentration_mmol_l: 10,
  qc_status: 'PASS',
  representative_value: 1413.2,
  k25_median: 1413.0,
};

function reason(e: ExperimentSummary): string {
  const check = calibrationCandidate(e);
  assert.ok(!check.ok, '应不可选');
  return check.reason;
}

test('PASS uses the representative value, WARN falls back to the median', () => {
  const pass = calibrationCandidate(base);
  assert.ok(pass.ok);
  assert.equal(pass.candidate.kappa25, 1413.2);
  assert.equal(pass.candidate.source, 'representative');

  const warn = calibrationCandidate({ ...base, qc_status: 'WARN', representative_value: null });
  assert.ok(warn.ok);
  assert.equal(warn.candidate.kappa25, 1413.0);
  assert.equal(warn.candidate.source, 'median');
});

test('blank sample (concentration 0) is a valid calibration point', () => {
  const blank = calibrationCandidate({ ...base, concentration_mmol_l: 0 });
  assert.ok(blank.ok);
  assert.equal(blank.candidate.concentration, 0);
});

test('ineligible experiments explain why they cannot be selected', () => {
  assert.equal(reason({ ...base, qc_status: 'FAIL' }), 'QC FAIL');
  assert.equal(reason({ ...base, qc_status: null }), 'QC 未判定');
  assert.equal(reason({ ...base, concentration_mmol_l: null }), '未填浓度');
  assert.equal(reason({ ...base, status: 'running' }), '进行中');
  assert.equal(reason({ ...base, status: 'aborted' }), '未正常停止');
  assert.equal(
    reason({ ...base, qc_status: 'WARN', representative_value: null, k25_median: null }),
    '无 κ25 代表值',
  );
});

test('calibration points feed the concentration axis one point per experiment', () => {
  const candidates = [0, 5, 10].flatMap((c, i) => {
    const check = calibrationCandidate({ ...base, id: i + 1, concentration_mmol_l: c, representative_value: 100 + c });
    return check.ok ? [check.candidate] : [];
  });
  assert.deepEqual(buildFitPoints(calibrationDataPoints(candidates), 'concentration'), [
    [0, 100],
    [5, 105],
    [10, 110],
  ]);
});

test('selectionKey ignores checking order', () => {
  assert.equal(selectionKey([12, 3, 7]), selectionKey(new Set([7, 12, 3])));
  assert.equal(selectionKey([12, 3, 7]), '3,7,12');
});
