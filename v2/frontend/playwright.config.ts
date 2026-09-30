import { defineConfig, devices } from '@playwright/test';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * E2E：按生产形态跑——后端（python -m ec）同源托管 npm run build 产出的 dist，模拟器加速。
 * 先 `npm run build`，再 `npx playwright test`。
 *   EC_PYTHON    装好 v2/backend/requirements.txt 的解释器（缺省 python3 / Windows 下 python）
 *   E2E_BROWSER  msedge / chrome 时用系统浏览器，缺省用 Playwright 自带 chromium
 */
const here = path.dirname(fileURLToPath(import.meta.url));
const python = process.env.EC_PYTHON ?? (process.platform === 'win32' ? 'python' : 'python3');
const port = 8011;
const channel = process.env.E2E_BROWSER;

export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  workers: 1, // 后端只有一个「当前测量」，用例按顺序跑
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    ...(channel ? { channel } : {}),
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `"${python}" -m ec`,
    cwd: path.resolve(here, '../backend'),
    url: `http://127.0.0.1:${port}/api/state`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      EC_PORT: String(port),
      EC_DB_PATH: path.join(os.tmpdir(), `ec-v2-e2e-${process.pid}.db`),
      EC_STATIC_DIR: path.resolve(here, 'dist'),
      EC_SIM_RATE_HZ: '10',
      EC_SIM_SETTLE_S: '0.2',
      EC_QC_WINDOW_S: '2',
    },
  },
});
