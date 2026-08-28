import assert from 'node:assert/strict';
import { test } from 'node:test';
import { errorFromControlResponse } from './controlError.ts';

test('failed control uses message or a fallback', () => {
  assert.equal(
    errorFromControlResponse({ ok: false, message: '实验已在进行中' }),
    '实验已在进行中',
  );
  assert.equal(errorFromControlResponse({ ok: false }), '控制请求失败');
});

test('ok informational message is not an error banner', () => {
  assert.equal(
    errorFromControlResponse({ ok: true, status: 'stopped', message: '当前没有运行中的实验' }),
    null,
  );
  assert.equal(errorFromControlResponse({ ok: true, status: 'idle' }), null);
});

test('ok persist-degraded message still shows as an error', () => {
  assert.match(
    errorFromControlResponse({
      ok: true,
      status: 'stopped',
      persistence: 'degraded',
      message: '落库失败：实时曲线仍在更新，但历史和导出将缺帧。请重启后端恢复落库。',
    }) ?? '',
    /落库失败/,
  );
});
