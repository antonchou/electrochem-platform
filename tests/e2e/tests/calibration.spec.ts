import { expect, test, type APIRequestContext } from '@playwright/test';

/**
 * 跨实验浓度标定（09-30 审查 #4）：单个实验只有一种浓度，浓度轴拟合只能跨实验做。
 * 用 API 跑 3 个不同浓度的实验（含 c=0 空白样），再在历史面板里勾选并拟合。
 */

const API = 'http://127.0.0.1:8000';

async function runExperiment(
  request: APIRequestContext,
  sampleId: string,
  concentration: number,
): Promise<number> {
  const res = await request.post(`${API}/api/experiment/start`, {
    data: { sample_id: sampleId, concentration_mmol_l: concentration },
  });
  const body = await res.json();
  expect(body.ok, JSON.stringify(body)).toBe(true);
  // ≥10 帧（10Hz × 1.5s）：停止时 QC 样本数足够，得到 PASS 代表值
  await new Promise((resolve) => setTimeout(resolve, 1500));
  await request.post(`${API}/api/experiment/stop`);
  return body.experiment_id as number;
}

test('跨实验标定：勾选 3 个浓度的实验 → 浓度轴拟合出线性标定 / Kohlrausch', async ({
  page,
  request,
}) => {
  await request.post(`${API}/api/experiment/reset`);
  const tag = Date.now().toString(36).toUpperCase();
  const ids: number[] = [];
  for (const c of [0, 5, 10]) ids.push(await runExperiment(request, `CAL_${tag}_${c}`, c));
  await request.post(`${API}/api/experiment/reset`);

  await page.goto('/');
  await page.getByTestId('btn-history').click();
  const panel = page.getByTestId('history-panel');
  await panel.getByTestId('btn-calibration-mode').click();
  await expect(panel.getByTestId('calibration-panel')).toBeVisible();

  for (const id of ids) await panel.getByTestId(`calib-check-${id}`).check();
  await expect(panel.getByTestId('calibration-summary')).toContainText('已选 3 个实验 · 3 种浓度');

  // 标定面板只开浓度轴
  await expect(panel.getByTestId('calib-fit-axis-concentration')).toBeVisible();
  await expect(panel.getByTestId('calib-fit-axis-time')).toHaveCount(0);

  await panel.getByTestId('calib-fit-btn-fit').click();
  const results = panel.getByTestId('calib-fit-results');
  await expect(results).toBeVisible();
  // c=0 空白样也在点集里，Kohlrausch 不得因此被跳过
  await expect(results).toContainText('Kohlrausch');
  await expect(results).toContainText('线性标定');
  await expect(panel.getByTestId('calibration-saved')).toContainText('calibration_');
});

test('跨实验标定：浓度不足 3 种时不能拟合', async ({ page, request }) => {
  await request.post(`${API}/api/experiment/reset`);
  const tag = Date.now().toString(36).toUpperCase();
  const a = await runExperiment(request, `CAL2_${tag}_A`, 5);
  const b = await runExperiment(request, `CAL2_${tag}_B`, 5);
  await request.post(`${API}/api/experiment/reset`);

  await page.goto('/');
  await page.getByTestId('btn-history').click();
  const panel = page.getByTestId('history-panel');
  await panel.getByTestId('btn-calibration-mode').click();
  await panel.getByTestId(`calib-check-${a}`).check();
  await panel.getByTestId(`calib-check-${b}`).check();
  await expect(panel.getByTestId('calibration-summary')).toContainText('至少需要 3 种');
  await expect(panel.getByTestId('calib-fit-concentration-note')).toBeVisible();
  await expect(panel.getByTestId('calib-fit-btn-fit')).toBeDisabled();
});
