/**
 * E2E 验收测试配置。
 * 自动拉起两个依赖服务：
 *  1) backend（FastAPI，端口 8000）
 *  2) frontend dev server（Vite，端口 5173）
 * 本地 reuseExistingServer=true：如果服务已在运行则复用。
 * CI 必须自行拉起，且用 PATH 上的 python（无本地 .venv）。
 *
 * 浏览器选择：
 *  - 默认使用 Playwright 自带的 Chromium（首次需 `npx playwright install chromium`）
 *  - 也可通过环境变量使用系统已安装的浏览器（免下载）：
 *      E2E_BROWSER=msedge  → 使用系统 Microsoft Edge
 *      E2E_BROWSER=chrome  → 使用系统 Google Chrome
 */
import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const channel = process.env.E2E_BROWSER;
const e2eOutputDir = path.join(tmpdir(), 'ec-e2e-results');
const inCI = Boolean(process.env.CI);
const configDir = path.dirname(fileURLToPath(import.meta.url));

function backendPython(): string {
  if (process.env.EC_E2E_PYTHON) return process.env.EC_E2E_PYTHON;
  const unix = path.join(configDir, '../../backend/.venv/bin/python');
  const win = path.join(configDir, '../../backend/.venv/Scripts/python.exe');
  if (existsSync(unix)) return unix;
  if (existsSync(win)) return win;
  return 'python3';
}

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  testIgnore: inCI ? ['**/screenshots.spec.ts', '**/stability.spec.ts'] : [],
  // 产物（trace/截图/HTML 报告）输出到系统临时目录，避免在项目目录产生大量待清理文件
  outputDir: e2eOutputDir,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...(channel ? { channel } : {}),
  },
  projects: [
    { name: channel ? `chromium-${channel}` : 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: [
    {
      command: `cd ../../backend && ${JSON.stringify(backendPython())} -m uvicorn app.main:app --host 127.0.0.1 --port 8000`,
      url: 'http://127.0.0.1:8000/health',
      env: {
        EC_ENABLE_DEBUG_ENDPOINTS: '1',
        EC_DB_PATH: path.join(e2eOutputDir, 'backend.db'),
      },
      reuseExistingServer: !inCI,
      timeout: 30_000,
    },
    {
      command: 'cd ../../frontend && npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: !inCI,
      timeout: 60_000,
    },
  ],
});
