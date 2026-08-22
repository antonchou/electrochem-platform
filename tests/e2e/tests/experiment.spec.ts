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

test('F03 V2 电极链路：U/I/T、G/κ(T)/κ25、校准与质量字段同时可见', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  const numericCards = [
    ['value-voltage', 'V'],
    ['value-current', 'mA'],
    ['value-temperature', '°C'],
    ['value-conductance', 'S'],
    ['value-kappa-t', 'μS/cm'],
    ['value-kappa25', 'μS/cm'],
  ] as const;
  for (const [testId, unit] of numericCards) {
    const card = page.getByTestId(testId);
    await expect(card).not.toContainText('--', { timeout: 8000 });
    await expect(card).toContainText(unit);
    const text = await card.innerText();
    expect(Number.isFinite(Number(text.match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/i)?.[0]))).toBe(true);
  }
  await expect(page.getByTestId('calib-status')).toContainText('CAL_MOCK_CONFIG');
  await expect(page.getByTestId('quality-status')).toContainText('SIMULATED');
  await expect(page.getByTestId('trace-range')).toContainText('R_100R_10K');
  await expect(page.getByTestId('trace-sensor-path')).toContainText('EC_IV_CELL_MOCK');
  await expect(page.getByTestId('trace-cell-constant')).toContainText('1 cm⁻¹');
  await expect(page.getByTestId('trace-compensation')).toContainText('linear');
  await expect(page.getByTestId('trace-compensation')).toContainText('0.02');

  // 稳定传感器连续两帧可能恰好显示同一舍入值；以采样点数增长证明数据仍在更新。
  const countBefore = Number(await page.getByTestId('stat-count').innerText());
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), { timeout: 3000 })
    .toBeGreaterThan(countBefore);
});

test('F03b 未校准帧：保留 Raw 数据与质量状态，不被误判为状态帧', async ({ page, request }) => {
  const response = await request.post(`${API}/api/experiment/start`, {
    data: { cell_constant_cm_inv: null, alpha_per_c: null },
  });
  expect(response.ok()).toBeTruthy();
  await waitForPoints(page, 1);
  await expect(page.getByTestId('value-kappa-t')).toContainText('--');
  await expect(page.getByTestId('value-kappa25')).toContainText('--');
  await expect(page.getByTestId('value-voltage')).not.toContainText('--');
  await expect(page.getByTestId('value-current')).not.toContainText('--');
  await expect(page.getByTestId('value-temperature')).not.toContainText('--');
  await expect(page.getByTestId('value-conductance')).not.toContainText('--');
  await expect(page.getByTestId('calib-status')).toContainText('未校准');
  await expect(page.getByTestId('quality-status')).toContainText('UNCALIBRATED');
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

test('F04b 实时曲线坐标：时间轴延伸且纵轴稳定覆盖读数', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await waitForPoints(page, 5);

  const chart = page.getByTestId('realtime-chart');
  await expect
    .poll(async () => Number(await chart.getAttribute('data-chart-x-max')), {
      timeout: 5000,
      message: '时间轴上限应随实验时间超过 1 秒',
    })
    .toBeGreaterThan(1);

  const yMin1 = Number(await chart.getAttribute('data-chart-y-min'));
  const yMax1 = Number(await chart.getAttribute('data-chart-y-max'));
  const ecText = await page.getByTestId('value-kappa-t').innerText();
  const kappaT = Number(ecText.match(/-?\d+(?:\.\d+)?/)?.[0]);
  expect(Number.isFinite(kappaT), '实时 κ(T) 卡片应包含可解析的数值').toBe(true);
  expect(kappaT).toBeGreaterThanOrEqual(yMin1);
  expect(kappaT).toBeLessThanOrEqual(yMax1);

  await page.waitForTimeout(1200);
  const yMin2 = Number(await chart.getAttribute('data-chart-y-min'));
  const yMax2 = Number(await chart.getAttribute('data-chart-y-max'));
  expect(yMin2, '同一轮实验中纵轴下限不应向上跳动').toBeLessThanOrEqual(yMin1);
  expect(yMax2, '同一轮实验中纵轴上限不应向下跳动').toBeGreaterThanOrEqual(yMax1);
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

test('F10 在线协议拒绝 V1 帧且页面保持可用', async ({ page }) => {
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
