/**
 * E2E 验收测试配置。
 * 自动拉起两个依赖服务：
 *  1) backend（FastAPI，端口 8000）
 *  2) frontend dev server（Vite，端口 5173）
 * reuseExistingServer=true：如果服务已在运行则复用（比如开发时自己起了服务）。
 *
 * 浏览器选择：
 *  - 默认使用 Playwright 自带的 Chromium（首次需 `npx playwright install chromium`）
 *  - 也可通过环境变量使用系统已安装的浏览器（免下载）：
 *      E2E_BROWSER=msedge  → 使用系统 Microsoft Edge
 *      E2E_BROWSER=chrome  → 使用系统 Google Chrome
 */
import { defineConfig, devices } from '@playwright/test';
import { tmpdir } from 'node:os';
import path from 'node:path';

const channel = process.env.E2E_BROWSER;
const e2eOutputDir = path.join(tmpdir(), 'ec-e2e-results');

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
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
      command:
        'cd ../../backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000',
      url: 'http://127.0.0.1:8000/health',
      env: {
        EC_ENABLE_DEBUG_ENDPOINTS: '1',
        EC_DB_PATH: path.join(e2eOutputDir, 'backend.db'),
      },
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      command: 'cd ../../frontend && npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
});
