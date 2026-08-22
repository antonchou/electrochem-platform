import { expect, test } from '@playwright/test';

/**
 * 化学公式拟合功能验收：停止实验后，选 X 轴（时间/温度）→ 选模型 → 拟合 → 出结果与曲线。
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

test('备选公式拟合：时间轴选模型→拟合→出结果表与曲线', async ({ page }) => {
  // 跑一轮实验并停止
  await page.getByTestId('btn-start').click();
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), { timeout: 8000 })
    .toBeGreaterThanOrEqual(3);
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');

  // 拟合区域可见，默认时间轴
  await expect(page.getByTestId('fit-area')).toBeVisible();
  await expect(page.getByTestId('fit-axis-time')).toHaveClass(/active/i);
  await expect(page.getByTestId('fit-model-first_order')).toBeVisible();

  // 取消勾选指数模型，只保留部分模型
  await page.getByTestId('fit-model-exponential').click();

  // 开始拟合
  await page.getByTestId('btn-fit').click();
  await expect(page.getByTestId('fit-results')).toBeVisible();

  // 结果表包含公式与 R²，且出现最优提示
  await expect(page.getByTestId('fit-results')).toContainText('线性');
  await expect(page.getByTestId('fit-results')).toContainText('R²');
  await expect(page.getByTestId('fit-results')).toContainText('最优：');
});

test('拟合中切换模型：旧响应不得覆盖当前选择', async ({ page }) => {
  await page.getByTestId('btn-start').click();
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), { timeout: 8000 })
    .toBeGreaterThanOrEqual(3);
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');

  let releaseRequest: (() => void) | undefined;
  let markRequestIntercepted: (() => void) | undefined;
  const requestIntercepted = new Promise<void>((resolve) => {
    markRequestIntercepted = resolve;
  });

  await page.route('**/api/analysis/fit', async (route) => {
    markRequestIntercepted?.();
    await new Promise<void>((resolve) => {
      releaseRequest = resolve;
    });
    await route.continue();
  });

  await page.getByTestId('btn-fit').click();
  await requestIntercepted;
  await expect(page.getByTestId('btn-fit')).toHaveText('拟合中…');

  await page.getByTestId('fit-model-exponential').click();
  await expect(page.getByTestId('btn-fit')).toBeEnabled();

  const oldResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/analysis/fit') && response.request().method() === 'POST',
  );
  releaseRequest?.();
  await oldResponse;

  await expect(page.getByTestId('fit-results')).toHaveCount(0);
});

test('温度轴：近恒温数据跳过无意义的 Arrhenius 结果', async ({ page }) => {
  // 跑一轮实验并停止（帧含温度数据）
  await page.getByTestId('btn-start').click();
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), { timeout: 8000 })
    .toBeGreaterThanOrEqual(3);
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');

  // 切到温度轴：模型池变为 线性温补/二次/Arrhenius
  await page.getByTestId('fit-axis-temperature').click();
  await expect(page.getByTestId('fit-model-arrhenius')).toBeVisible();
  await expect(page.getByTestId('fit-model-kohlrausch')).toHaveCount(0); // 跨轴模型不出现

  // Mock 温度波动小于 1 °C，界面应解释约束，后端不得伪造活化能结果
  await expect(page.getByTestId('fit-arrhenius-note')).toContainText('温度跨度不足');
  await page.getByTestId('btn-fit').click();
  await expect(page.getByTestId('fit-results')).toBeVisible();
  await expect(page.getByTestId('fit-results')).not.toContainText('Ea_kJ_mol');
});

test('浓度轴：切到浓度→线性标定/Kohlrausch 模型可用', async ({ page }) => {
  // 跑一轮实验并停止
  await page.getByTestId('btn-start').click();
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), { timeout: 8000 })
    .toBeGreaterThanOrEqual(3);
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');

  // 切到浓度轴：模型池变为 线性标定/二次/Kohlrausch
  await page.getByTestId('fit-axis-concentration').click();
  await expect(page.getByTestId('fit-axis-concentration')).toHaveClass(/active/i);
  await expect(page.getByTestId('fit-model-kohlrausch')).toHaveCount(1);
  await expect(page.getByTestId('fit-model-kohlrausch')).toBeVisible();
  await expect(page.getByTestId('fit-model-linear')).toBeVisible();
  await expect(page.getByTestId('fit-model-quadratic')).toBeVisible();

  // 跨轴隔离：时间轴专属模型（一阶指数饱和）不出现
  await expect(page.getByTestId('fit-model-first_order')).toHaveCount(0);

  // 浓度轴占位提示可见（当前帧无浓度字段）
  await expect(page.getByTestId('fit-concentration-note')).toBeVisible();
  await expect(page.getByTestId('btn-fit')).toBeDisabled();
});

test('历史详情页：复用化学公式拟合', async ({ page }) => {
  // 跑一轮实验并停止
  await page.getByTestId('btn-start').click();
  await expect
    .poll(async () => Number(await page.getByTestId('stat-count').innerText()), { timeout: 8000 })
    .toBeGreaterThanOrEqual(3);
  await page.getByTestId('btn-stop').click();
  await expect(page.getByTestId('experiment-status')).toHaveText('已停止');

  // 打开历史 → 进最新一条详情
  await page.getByTestId('btn-history').click();
  const panel = page.getByTestId('history-panel');
  await expect(panel).toBeVisible();
  await panel.locator('[data-testid^="history-item-"]').first().click();

  // 详情页拟合区可见（testid 前缀 hist-fit，与主页 fit-* 区分）
  await expect(panel.getByTestId('hist-fit-area')).toBeVisible();

  // 默认时间轴直接拟合 → 出结果表
  await panel.getByTestId('hist-fit-btn-fit').click();
  await expect(panel.getByTestId('hist-fit-results')).toBeVisible();
  await expect(panel.getByTestId('hist-fit-results')).toContainText('最优：');

  // 切温度轴：模型池正确切换
  await panel.getByTestId('hist-fit-axis-temperature').click();
  await expect(panel.getByTestId('hist-fit-model-arrhenius')).toBeVisible();
  await expect(panel.getByTestId('hist-fit-model-kohlrausch')).toHaveCount(0);
});
