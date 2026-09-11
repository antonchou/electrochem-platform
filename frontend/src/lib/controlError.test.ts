import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { ControlResponse } from '../types/protocol.ts';
import { errorFromControlResponse } from './controlError.ts';

// 控制接口哪些文案进错误横幅（类型与实现共用同一契约）
function res(partial: Partial<ControlResponse> & { ok: boolean }): ControlResponse {
  return { status: 'idle', ...partial };
}

test('failed control uses message or a fallback', () => {
  assert.equal(
    errorFromControlResponse(res({ ok: false, message: '实验已在进行中' })),
    '实验已在进行中',
  );
  assert.equal(errorFromControlResponse(res({ ok: false })), '控制请求失败');
});

test('ok informational message is not an error banner', () => {
  assert.equal(
    errorFromControlResponse(res({ ok: true, status: 'stopped', message: '当前没有运行中的实验' })),
    null,
  );
  assert.equal(errorFromControlResponse(res({ ok: true, status: 'idle' })), null);
});

test('ok persist-degraded message still shows as an error', () => {
  assert.match(
    errorFromControlResponse(
      res({
        ok: true,
        status: 'stopped',
        persistence: 'degraded',
        message: '落库失败：实时曲线仍在更新，但历史和导出将缺帧。请重启后端恢复落库。',
      }),
    ) ?? '',
    /落库失败/,
  );
});
