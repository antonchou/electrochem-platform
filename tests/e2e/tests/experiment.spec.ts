import { expect, test } from '@playwright/test';

/**
 * 面向《Web界面开发任务书》验收标准的功能测试（F01–F10）与性能冒烟（P01–P04）。
 * 运行前提：backend(8000) 与 frontend(5173) 已由 playwright.config 拉起。
 */

const API = 'http://127.0.0.1:8000';

async function resetExperiment(request: import('@playwright/test').APIRequestContext) {
  await request.post(`${API}/api/experiment/reset`);
}

async function waitForPoints(page: import('@playwright/test').Page, min: number, timeout = 8000) {
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), {
      timeout,
      message: `采样点数应达到 ${min}`,
    })
    .toBeGreaterThanOrEqual(min);
}

test.beforeEach(async ({ page, request }) => {
  await resetExperiment(request);
  await page.goto('/');
  // 等待连接成功（F01：启动无阻断性报错；F05：WebSocket 已连接）
  await expect(page.getByTestId('connection-status')).toHaveText('已连接');
});

// ---------------- F 系列：功能验收 ----------------

test('F01 项目启动：页面可加载且无阻断报错', async ({ page }) => {
  await expect(page.getByTestId('realtime-chart')).toBeVisible();
  await expect(page.getByTestId('btn-start')).toBeEnabled();
});

test('F02 开始实验：进入 Running 并接收数据', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('运行中');
  await waitForPoints(page, 1);
  // 运行中"开始"按钮应禁用，防止重复触发
  await expect(page.getByTestId('btn-start')).toBeDisabled();
});

test('F03 实时数值：EC/温度持续更新且单位正确', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await expect(page.getByTestId('value-ec')).not.toContainText('--', { timeout: 8000 });
  const ec = page.getByTestId('value-ec');
  await expect(ec).toContainText('μS/cm');
  await expect(page.getByTestId('value-temperature')).toContainText('°C');

  const t1 = await ec.innerText();
  await page.waitForTimeout(600);
  const t2 = await ec.innerText();
  expect(t1).not.toBe(t2); // 数值在持续更新
});

test('F04 实时曲线：数据自动追加，无需刷新页面', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await waitForPoints(page, 1);
  const c1 = Number(await page.getByTestId('stat-count').innerText());
  await page.waitForTimeout(1500);
  const c2 = Number(await page.getByTestId('stat-count').innerText());
  expect(c2).toBeGreaterThan(c1);
  // 页面未刷新：连接状态保持不变
  await expect(page.getByTestId('connection-status')).toHaveText('已连接');
});

test('F05 WebSocket：连接指定地址并解析约定 JSON', async ({ page }) => {
  await expect(page.getByTestId('connection-status')).toHaveText('已连接');
  await expect(page.getByTestId('connection-panel')).toContainText('后端(WS)');
});

test('F06 停止实验：不再追加数据，保留当前曲线', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await waitForPoints(page, 3);
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');
  const c1 = Number(await page.getByTestId('stat-count').innerText());
  await page.waitForTimeout(1000);
  const c2 = Number(await page.getByTestId('stat-count').innerText());
  expect(c2).toBe(c1); // 停止后点数不再增加
});

test('F07 重新开始：清空上一轮数据并启动新实验', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await waitForPoints(page, 3);
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');

  await page.getByTestId('btn-reset').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('空闲');
  await expect(page.getByTestId('stat-count')).toHaveText('0');

  await page.getByTestId('btn-start').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('运行中');
  await waitForPoints(page, 1);
});

test('F08 断线检测：断开后 3 秒内出现明确异常状态', async ({ page }) => {
  await expect(page.getByTestId('connection-status')).toHaveText('已连接');
  await page.request.post(`${API}/api/debug/close-connections`);
  await expect(page.getByTestId('connection-status')).toHaveText(/已断开|重连中/, { timeout: 3000 });
  // 出现可理解的错误提示（而非仅控制台报错）
  await expect(page.getByTestId('error-banner')).toBeVisible({ timeout: 3000 });
});

test('F09 恢复连接：后端恢复后自动重连', async ({ page }) => {
  await page.request.post(`${API}/api/debug/close-connections`);
  await expect(page.getByTestId('connection-status')).toHaveText(/已断开|重连中/, { timeout: 3000 });
  // 自动重连（指数退避 ≤5s）后恢复
  await expect(page.getByTestId('connection-status')).toHaveText('已连接', { timeout: 15_000 });
});

test('F10 异常数据：坏帧不导致页面崩溃', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await waitForPoints(page, 1);
  await page.request.post(`${API}/api/debug/bad-frame`);
  // 出现提示
  await expect(page.getByTestId('error-banner')).toBeVisible({ timeout: 5000 });
  // 页面仍可操作：正常停止
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');
});

// ---------------- P 系列：实时性与稳定性 ----------------

test('P02 持续数据流：10Hz 连续输入', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await waitForPoints(page, 1);
  const c1 = Number(await page.getByTestId('stat-count').innerText());
  await page.waitForTimeout(1500);
  const c2 = Number(await page.getByTestId('stat-count').innerText());
  // 10Hz×1.5s ≈ 15 点；留出网络/渲染余量，至少 10 点
  expect(c2 - c1).toBeGreaterThanOrEqual(10);
});

test('P04 数据规模：承载 10000 点不卡死', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await waitForPoints(page, 1);
  // 快速注入 1 万帧（backend debug 接口）
  await page.request.post(`${API}/api/debug/burst?count=10000`);
  await waitForPoints(page, 10000, 20_000);
  // 页面仍可交互：可停止，且无阻断
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');
});
