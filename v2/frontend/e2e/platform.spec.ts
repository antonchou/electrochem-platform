import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

// 判稳窗口 2 s、模拟器 10 Hz、换溶液趋稳 0.2 s（见 playwright.config.ts）。

async function ensureIdle(request: APIRequestContext) {
  await request.post('/api/measurements/current/stop'); // 没有进行中的测量时是 409，忽略
  await request.post('/api/simulator', { data: { fault: 'none' } });
}

async function measureViaApi(request: APIRequestContext, sampleName: string, concentration: number | null) {
  const started = await request.post('/api/measurements', {
    data: { sample_name: sampleName, concentration_mmol_l: concentration },
  });
  expect(started.status()).toBe(201);
  await new Promise((resolve) => setTimeout(resolve, 4_000)); // 换溶液的趋稳要落在 2 s 判稳窗口之外
  const stopped = await request.post('/api/measurements/current/stop');
  expect(stopped.ok()).toBeTruthy();
  return (await stopped.json()) as { id: number; qc: { verdict: string } };
}

async function openTab(page: Page, name: string) {
  await page.getByRole('tab', { name }).click();
}

test.beforeEach(async ({ request }) => {
  await ensureIdle(request);
});

test('监视：设备状态与实时读数', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('device-status')).toContainText('模拟导电池');
  await expect(page.getByTestId('kcell-status')).toContainText('未标定');
  await expect(page.getByTestId('mode')).toHaveText('监视中（不记录）');
  await expect(page.getByTestId('kappa25')).not.toHaveText('—');
  await expect(page.getByTestId('live-chart').locator('canvas')).toBeVisible();
});

test('开始按钮的校验：没有样品名不能开始，浓度要是数字', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('start')).toBeDisabled();
  await page.getByTestId('sample-name').fill('x');
  await expect(page.getByTestId('start')).toBeEnabled();
  await page.getByTestId('concentration').fill('abc');
  await expect(page.getByText('浓度须为 ≥ 0 的数字，或留空')).toBeVisible();
  await expect(page.getByTestId('start')).toBeDisabled();
});

test('一次完整测量：开始 → 实时判稳 → 停止 → 结果', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('sample-name').fill('KCl 10 mM');
  await page.getByTestId('concentration').fill('10');
  await page.getByTestId('start').click();
  await expect(page.getByTestId('mode')).toContainText('记录中');
  await expect(page.getByTestId('sample-name')).toBeDisabled();
  await expect(page.getByTestId('live-verdict')).toHaveText('稳定', { timeout: 15_000 });
  await page.getByTestId('stop').click();
  const result = page.getByTestId('last-result');
  await expect(result).toContainText('KCl 10 mM');
  await expect(page.getByTestId('result-verdict')).toHaveText('稳定');
  await expect(page.getByTestId('representative')).toContainText('µS/cm');
  await expect(page.getByTestId('mode')).toHaveText('监视中（不记录）');
});

test('刷新页面后仍在记录中的测量能接上（服务端快照）', async ({ page, request }) => {
  await request.post('/api/measurements', { data: { sample_name: 'reload', concentration_mmol_l: null } });
  await page.goto('/');
  await expect(page.getByTestId('mode')).toContainText('记录中');
  await page.reload();
  await expect(page.getByTestId('mode')).toContainText('记录中');
  await expect(page.getByTestId('sample-name')).toHaveValue('reload');
  await page.getByTestId('stop').click();
  await expect(page.getByTestId('last-result')).toContainText('reload');
});

test('记录：详情、导出、比较与浓度拟合', async ({ page, request }) => {
  const ids = [];
  for (const c of [1, 5, 10]) ids.push((await measureViaApi(request, `KCl ${c} mM`, c)).id);

  await page.goto('/');
  await openTab(page, '记录');
  await page.getByTestId(`record-${ids[2]}`).click();
  const detail = page.getByTestId('record-detail');
  await expect(detail).toContainText(`测量 #${ids[2]} · KCl 10 mM`);
  await expect(detail.getByTestId('detail-chart').locator('canvas')).toBeVisible();

  const csv = await request.get(`/api/measurements/${ids[2]}/frames.csv`);
  expect(csv.ok()).toBeTruthy();
  expect((await csv.text()).split('\n')[0]).toBe(
    'seq,t_s,timestamp_utc,device_seq,device_ms,voltage_v,current_a,temperature_c,conductance_s,kappa_t_us_cm,kappa25_us_cm,flags',
  );

  for (const id of ids) await page.getByLabel(`选择测量 ${id}`).check();
  const compare = page.getByTestId('compare');
  await expect(compare).toContainText('比较（3 次测量）');
  await compare.getByTestId('fit-concentration').click();
  const result = page.getByTestId('concentration-result');
  await expect(result).toContainText('Kohlrausch');
  await expect(result).toContainText('Λ0 =');
  await expect(result.getByTestId('concentration-chart').locator('canvas')).toBeVisible();
});

test('标定：用标准液测量求 Kcell，之后的测量自动使用', async ({ page, request }) => {
  const standard = await measureViaApi(request, 'KCl 0.01 mol/L 标准液', 10);
  expect(standard.qc.verdict).not.toBe('FAIL');

  await page.goto('/');
  await openTab(page, '标定');
  await page.getByTestId('cal-measurement-0').selectOption(String(standard.id));
  await page.getByTestId('cal-standard-0').selectOption('KCl 0.01 mol/L');
  await page.getByPlaceholder('操作者').fill('e2e');
  await page.getByTestId('calibrate').click();

  const kcell = Number(await page.getByTestId('kcell-value').textContent());
  expect(kcell).toBeGreaterThan(1.01); // 模拟器的真 Kcell = 1.02
  expect(kcell).toBeLessThan(1.03);
  await expect(page.getByTestId('kcell-status')).toContainText('标定 #');

  const after = await measureViaApi(request, 'KCl 0.01 mol/L 复测', 10);
  const detail = await (await request.get(`/api/measurements/${after.id}`)).json();
  expect(detail.calibration_id).not.toBeNull();
  expect(detail.qc.representative_kappa25).toBeGreaterThan(1400);
  expect(detail.qc.representative_kappa25).toBeLessThan(1425);
});

test('故障：电极出水时显示开路标志', async ({ page, request }) => {
  await page.goto('/');
  await request.post('/api/simulator', { data: { fault: 'air' } });
  await expect(page.locator('[data-flag="OPEN_CIRCUIT"]').first()).toBeVisible();
  await request.post('/api/simulator', { data: { fault: 'none' } });
  await expect(page.locator('[data-flag="OPEN_CIRCUIT"]')).toHaveCount(0);
});
