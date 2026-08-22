import { expect, test } from '@playwright/test';

/**
 * Phase 7 验收：历史实验 + 样品溯源 + 导出。
 * 流程：带样品号跑一轮 → 停止 → 结果区显示样品/导出 → 历史面板可见 →
 *       详情含样品与帧 → CSV 可下载且内容正确。
 */

const API = 'http://127.0.0.1:8000';

async function resetExperiment(request: import('@playwright/test').APIRequestContext) {
  await request.post(`${API}/api/experiment/reset`);
}

test.beforeEach(async ({ page, request }) => {
  await resetExperiment(request);
  await page.goto('/');
  await expect(page.getByTestId('connection-status')).toHaveText('已连接');
});

test('Phase7 历史实验全流程：样品溯源 + 导出 CSV', async ({ page }) => {
  // 1) 带样品号开始一轮实验
  await page.getByTestId('input-sample').fill('NACL_004');
  await page.getByTestId('btn-start').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('运行中');
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), { timeout: 8000 })
    .toBeGreaterThanOrEqual(3);

  // 2) 停止
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');

  // 3) 结果区显示样品与导出按钮
  await expect(page.getByTestId('result-sample')).toContainText('NACL_004');
  await expect(page.getByTestId('btn-export-current')).toBeVisible();

  // 4) 打开历史面板，最新一条应含本样品
  await page.getByTestId('btn-history').click();
  await expect(page.getByTestId('history-panel')).toBeVisible();
  const firstItem = page.locator('[data-testid^="history-item-"]').first();
  await expect(firstItem).toContainText('NACL_004');

  // 5) 进入详情：样品表 + 曲线区
  await firstItem.click();
  await expect(page.getByTestId('btn-export-csv')).toBeVisible();
  await expect(page.getByTestId('history-panel')).toContainText('NACL_004');
  await expect(page.getByTestId('history-panel')).toContainText('帧数');

  // 6) CSV 内容正确：改用 API 请求验证
  //    （Edge channel 下 `<a download>` 由 Edge 下载中心处理，不触发 Playwright
  //     的 download 事件；后端导出逻辑用 HTTP 直接断言更稳。）
  const href = await page.getByTestId('btn-export-csv').getAttribute('href');
  expect(href).toMatch(/\/api\/experiments\/\d+\/export\.csv$/);
  const res = await page.request.get(href!);
  expect(res.ok()).toBeTruthy();
  const csv = await res.text();
  expect(csv).toContain('legacy_ec_us_cm');
  expect(csv).toContain('voltage_raw_v');
  expect(csv).toContain('kappa_t_us_cm');
  expect(csv).toContain('NACL_004');
});
