import { expect, test } from '@playwright/test';

/**
 * 交付物 #5：页面截图（运行中 / 停止 / 断线 三个典型状态）。
 * 运行：npx playwright test tests/screenshots.spec.ts
 */

const API = 'http://127.0.0.1:8000';

test('截取运行中 / 停止 / 断线三状态', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('connection-status')).toHaveText('已连接');

  // 1) 运行中：开始后采集若干点，等曲线画出
  await page.getByTestId('btn-start').click();
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), { timeout: 8000 })
    .toBeGreaterThanOrEqual(8);
  await page.waitForTimeout(500);
  await page.screenshot({ path: '../../docs/screenshots/1-running.png', fullPage: true });

  // 2) 停止：停止后保留曲线与结果区
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');
  await expect(page.getByTestId('result-panel')).toBeVisible();
  await page.waitForTimeout(200);
  await page.screenshot({ path: '../../docs/screenshots/2-stopped.png', fullPage: true });

  // 3) 断线：强制断开，捕获异常状态与提示
  await page.request.post(`${API}/api/debug/close-connections`);
  await expect(page.getByTestId('connection-status')).toHaveText(/已断开|重连中/, { timeout: 3000 });
  await page.screenshot({ path: '../../docs/screenshots/3-disconnected.png', fullPage: true });
});

test('截取历史实验面板（Phase 7）', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('connection-status')).toHaveText('已连接');

  // 跑一轮实验并停止，产生历史数据
  await page.getByTestId('input-sample').fill('NACL_004');
  await page.getByTestId('btn-start').click();
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), { timeout: 8000 })
    .toBeGreaterThanOrEqual(5);
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');

  // 打开历史面板，点进最新一条详情（含样品表 + 静态曲线）
  await page.getByTestId('btn-history').click();
  await expect(page.getByTestId('history-panel')).toBeVisible();
  await page.locator('[data-testid^="history-item-"]').first().click();
  await expect(page.getByTestId('btn-export-csv')).toBeVisible();
  await page.waitForTimeout(400);
  await page.screenshot({ path: '../../docs/screenshots/4-history.png', fullPage: true });
});
