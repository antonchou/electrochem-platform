import type { APIRequestContext } from '@playwright/test';

/** E2E 后端端口与地址：playwright.config.ts 的 webServer 与各 spec 共用这一处。 */
export const BACKEND_PORT = 8000;
export const API = `http://127.0.0.1:${BACKEND_PORT}`;

/** 用例之间复位后端实验状态（idle），避免上一个用例的实验上下文泄漏。 */
export async function resetExperiment(request: APIRequestContext): Promise<void> {
  await request.post(`${API}/api/experiment/reset`);
}
