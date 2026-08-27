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

test('F03 实时数值：κ25/温度持续更新且单位正确', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await expect(page.getByTestId('value-kappa25-num')).not.toHaveText('--', { timeout: 8000 });
  await expect(page.getByTestId('value-kappa25')).toContainText('μS/cm');
  await expect(page.getByTestId('value-temperature')).toContainText('°C');

  const kappaText = await page.getByTestId('value-kappa25-num').innerText();
  const temperatureText = await page.getByTestId('value-temperature-num').innerText();
  expect(Number.isFinite(Number(kappaText))).toBe(true);
  expect(Number.isFinite(Number(temperatureText))).toBe(true);

  // 稳定传感器连续两帧可能恰好显示同一舍入值；以采样点数增长证明数据仍在更新。
  const countBefore = Number(await page.getByTestId('stat-count').innerText());
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), { timeout: 3000 })
    .toBeGreaterThan(countBefore);
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

test('F04b 实时波形坐标：时间轴延伸且电压轴稳定覆盖读数', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await waitForPoints(page, 5);

  const chart = page.getByTestId('realtime-chart');
  await expect
    .poll(async () => Number(await chart.getAttribute('data-chart-x-max')), {
      timeout: 5000,
      message: '时间轴上限应随实验时间超过 1 秒',
    })
    .toBeGreaterThan(1);

  const vMin1 = Number(await chart.getAttribute('data-chart-v-min'));
  const vMax1 = Number(await chart.getAttribute('data-chart-v-max'));
  const voltage = Number(await page.getByTestId('value-voltage-num').innerText());
  expect(Number.isFinite(voltage), '实时电压卡片应包含可解析的数值').toBe(true);
  expect(voltage).toBeGreaterThanOrEqual(vMin1);
  expect(voltage).toBeLessThanOrEqual(vMax1);

  await page.waitForTimeout(1200);
  const vMin2 = Number(await chart.getAttribute('data-chart-v-min'));
  const vMax2 = Number(await chart.getAttribute('data-chart-v-max'));
  expect(vMin2, '同一轮实验中电压轴下限不应向上跳动').toBeLessThanOrEqual(vMin1);
  expect(vMax2, '同一轮实验中电压轴上限不应向下跳动').toBeGreaterThanOrEqual(vMax1);
});

test('F05 WebSocket：连接指定地址并解析约定 JSON', async ({ page }) => {
  await expect(page.getByTestId('connection-status')).toHaveText('已连接');
  await expect(page.getByTestId('connection-panel')).toContainText('后端(WS)');
});

test('开始实验：浓度写入结果区，停止后显示 QC', async ({ page }) => {
  await page.getByTestId('input-sample').fill('NACL_010');
  await page.getByTestId('input-concentration').fill('10');
  await page.getByTestId('btn-start').click();
  await waitForPoints(page, 12);
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');

  await expect(page.getByTestId('result-panel')).toBeVisible();
  await expect(page.getByTestId('result-sample')).toContainText('NACL_010');
  await expect(page.getByTestId('result-concentration')).toContainText('10');
  await expect(page.getByTestId('result-qc-status')).toHaveText(/PASS|WARN|FAIL/);
  await expect
    .poll(async () => page.getByTestId('result-qc-value').innerText(), { timeout: 5000 })
    .not.toMatch(/^--\s*$/);
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

test('F07 停止后续跑同一实验；重新开始才开新实验', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await waitForPoints(page, 3);
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');
  const href1 = await page.getByTestId('btn-export-current').getAttribute('href');
  const countAtStop = Number(await page.getByTestId('stat-count').innerText());

  await expect(page.getByTestId('btn-start')).toHaveText('继续实验');
  await page.getByTestId('btn-start').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('运行中');
  // 续跑不得把已有曲线点数清零（只拟合最后一段的根因）
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), { timeout: 3000 })
    .toBeGreaterThanOrEqual(countAtStop);
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), { timeout: 8000 })
    .toBeGreaterThan(countAtStop);

  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');
  const href2 = await page.getByTestId('btn-export-current').getAttribute('href');
  expect(href2).toBe(href1);

  await page.getByTestId('btn-reset').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('运行中');
  await waitForPoints(page, 1);
  await page.getByTestId('btn-stop').click();
  const href3 = await page.getByTestId('btn-export-current').getAttribute('href');
  expect(href3).not.toBe(href1);
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

test('I-V 主图：恒压模拟不强制给出线性结论，仍给出平均电导', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await waitForPoints(page, 8);
  await expect(page.getByTestId('iv-chart')).toBeVisible();
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');
  await expect(page.getByTestId('iv-note')).toContainText('不是电压扫描');
  await expect(page.getByTestId('iv-equation')).toHaveText('暂无线性方程');
  await expect(page.getByTestId('iv-r2')).toHaveText('R² = --');
  await expect(page.getByTestId('iv-g')).not.toHaveText('--');
  await expect(page.getByTestId('iv-kappa')).not.toHaveText('--');
});

test('溶液比较：从本页第一次实验起算，不带入历史样品', async ({ page }) => {
  await expect(page.getByTestId('compare-empty')).toBeVisible();
  await page.getByTestId('input-sample').fill('NACL_010');
  await page.getByTestId('btn-start').click();
  await waitForPoints(page, 8);
  await expect(page.getByTestId('stat-exc-freq')).toContainText('交流');
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('solution-compare')).toBeVisible();
  await expect(page.getByTestId('compare-table')).toContainText('NACL_010');
  await expect(page.getByTestId('compare-table')).not.toContainText('蒸馏水');
  // 同一后端库里 F07 等用例已经写过 BLANK；比较图不得把那些历史柱带进来
  await expect(page.getByTestId('compare-table')).not.toContainText('BLANK');
  await expect(page.getByTestId('compare-simulated-note')).toBeVisible();
});

test('诊断区仍保留 EC-t，默认不是主图', async ({ page }) => {
  await expect(page.getByTestId('realtime-chart')).toBeVisible();
  await expect(page.getByTestId('ec-t-chart')).toBeHidden();
  await page.getByTestId('diagnostics-panel').locator('summary').click();
  await expect(page.getByTestId('ec-t-chart')).toBeVisible();
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
