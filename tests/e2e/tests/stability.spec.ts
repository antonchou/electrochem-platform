import { expect, test } from '@playwright/test';
import { API } from './backend';

/**
 * P03 长时间运行稳定性（30 分钟）。
 * 默认 30 分钟；可用环境变量缩短验证：STABILITY_MINUTES=2 npx playwright test -g "P03"
 *
 * 每 15 秒做一次探活：
 *  - 运行时长持续增长（页面未冻结、数据流未断；不看点数：缓冲封顶后点数不变）
 *  - 实验状态仍为运行中、WebSocket 仍连接
 * 结束后仍可正常停止实验（页面未失效）。
 */
const MINUTES = Number(process.env.STABILITY_MINUTES || '0');
const CHECK_EVERY_MS = 15_000;

test(`P03 长时间运行稳定性（${MINUTES} 分钟）`, async ({ page }) => {
  // 默认跳过：完整 30 分钟长跑需显式设置 STABILITY_MINUTES
  test.skip(MINUTES <= 0, 'P03 长跑需显式设置 STABILITY_MINUTES（如 2 / 30）');
  test.setTimeout((MINUTES + 3) * 60_000);

  await page.goto('/');
  await expect(page.getByTestId('connection-status')).toHaveText('已连接');
  await page.getByTestId('btn-start').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('运行中');

  const deadline = Date.now() + MINUTES * 60_000;
  let prevDuration = 0;

  while (Date.now() < deadline) {
    await page.waitForTimeout(CHECK_EVERY_MS);

    // 运行时长（最新帧时间）持续增长：数据流未断、页面未冻结。
    // 不看采样点数：前端缓冲封顶 2 万点（10Hz 约 33 分钟）后点数不再变化，更长的长跑会误报。
    const duration = Number.parseFloat(await page.getByTestId('stat-duration').innerText());
    expect(duration, '运行时长应持续增长').toBeGreaterThan(prevDuration);
    prevDuration = duration;

    // 实验仍运行、连接仍在、页面未崩溃
    await expect(page.getByTestId('experiment-status')).toHaveText('运行中');
    await expect(page.getByTestId('connection-status')).toHaveText('已连接');
    await expect(page.getByTestId('realtime-chart')).toBeVisible();
  }

  // 结束时页面仍可交互
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');
  await page.request.post(`${API}/api/experiment/reset`);
});
