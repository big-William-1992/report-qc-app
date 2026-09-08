// @ts-check
const { test, expect } = require('@playwright/test');

const BASE = 'http://127.0.0.1:8500';
// 固定 E2E 测试账号（与 qc.spec.js 一致，共享同一独立 DB，建号失败则登录复用）
const TEST_EMP = 'e2e_fixed';
const TEST_PWD = 'e2e-pass-123';

/** 授权前置：免责 + 建号（失败则已存在）+ 登录，返回 token。 */
async function bootstrapAuth(request) {
  await request.post(`${BASE}/api/v1/license/disclaimer`, { data: {} });
  await request.post(`${BASE}/api/v1/accounts`, {
    data: { emp_id: TEST_EMP, password: TEST_PWD, name: 'E2E 样本' },
  }).catch(() => {});
  const login = await request.post(`${BASE}/api/v1/accounts/login`, {
    data: { emp_id: TEST_EMP, password: TEST_PWD },
  });
  const body = await login.json();
  return body.data && (body.data.token || body.data.access_token) || '';
}

test.describe('样本库 E2E', () => {
  test('授权后样本库页可正常渲染加载列表', async ({ page, request }) => {
    const token = await bootstrapAuth(request);
    expect(token).toBeTruthy();

    await page.addInitScript((tok) => {
      localStorage.setItem('xy-token', tok);
      localStorage.setItem('xy-emp', 'e2e_s');
      localStorage.setItem('xy-onboarding-done', '1');
    }, token);

    await page.goto('/');
    await page.waitForTimeout(800);

    // 进入样本库页（点击导航 cell，确保走 switchPage 激活 #page-samples）
    const nav = page.locator('.nav-cell[data-page="samples"]');
    if (await nav.count()) {
      await nav.first().click();
    } else {
      await page.evaluate(() => { try { window.gotoPage && gotoPage('samples'); } catch (e) {} });
    }
    await page.waitForTimeout(1000);

    // 样本库页面容器应存在且可见（#page-samples 被激活）
    const samplesPage = page.locator('#page-samples');
    await expect(samplesPage).toBeVisible({ timeout: 5000 });
    // 样本库表格容器也应存在
    await expect(page.locator('#samplesTable')).toBeAttached({ timeout: 5000 });
  });
});
