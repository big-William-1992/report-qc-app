// @ts-check
const { test, expect } = require('@playwright/test');

const BASE = 'http://127.0.0.1:8500';
// 固定 E2E 测试账号：首个测试建号，后续测试直接登录复用（E2E server 共享同一独立 DB）
const TEST_EMP = 'e2e_fixed';
const TEST_PWD = 'e2e-pass-123';

/**
 * 授权闸门前置：接受免责声明 → 建号（失败则账号已存在，忽略）→ 登录拿 token。
 * 通过 API 完成（避免依赖 UI 闸门流），返回 { token, empId }。
 */
async function bootstrapAuth(request) {
  // 接受免责
  await request.post(`${BASE}/api/v1/license/disclaimer`, { data: {} });
  // 创建账号（首个账号免鉴权；已存在则 401，忽略）
  await request.post(`${BASE}/api/v1/accounts`, {
    data: { emp_id: TEST_EMP, password: TEST_PWD, name: 'E2E 测试' },
  }).catch(() => {});
  // 登录
  const login = await request.post(`${BASE}/api/v1/accounts/login`, {
    data: { emp_id: TEST_EMP, password: TEST_PWD },
  });
  const body = await login.json();
  return {
    token: body.data && (body.data.token || body.data.access_token) || '',
    empId: TEST_EMP,
  };
}

test.describe('报告质控 E2E', () => {
  test('授权前置 + 质控：输入含错别字报告，点运行质控应产出发现', async ({ page, request }) => {
    const auth = await bootstrapAuth(request);
    expect(auth.token).toBeTruthy();

    // 注入鉴权 token 后访问（同源 localStorage）
    await page.addInitScript(({ token, emp }) => {
      localStorage.setItem('xy-token', token);
      localStorage.setItem('xy-emp', emp);
    }, { token: auth.token, emp: auth.empId });

    await page.goto('/');
    await page.waitForTimeout(800);

    // 若出现首次引导浮层，关闭它（避免遮挡质控页）
    const onboard = page.locator('#onboardingOverlay');
    if (await onboard.isVisible().catch(() => false)) {
      await page.evaluate(() => localStorage.setItem('xy-onboarding-done', '1'));
      await onboard.evaluate(el => el.style.display = 'none').catch(() => {});
    }

    // 进入质控页（点击导航『报告质控』cell，走前端 gotoPage 流程）
    await page.locator('[data-page="qc"], .nav-cell:has-text("质控")').first().click()
      .catch(async () => {
        // 若导航不可点，尝试直接调 gotoPage
        await page.evaluate(() => { try { window.gotoPage && gotoPage('qc'); } catch (e) {} });
      });
    await page.waitForTimeout(600);

    const f = page.locator('#findingsText');
    const i = page.locator('#impressionText');
    await f.fill('右肺上叶见一姐姐状高密度影，大小约 3cm。');
    await i.fill('右肺结节，建议复查。');

    // 点击运行质控（确保可见后再点）
    await page.locator('#btnRunQc').waitFor({ state: 'visible', timeout: 5000 });
    await page.locator('#btnRunQc').click();
    await page.waitForTimeout(1500);

    // 结果列表应出现（findingList 有子元素）
    const listItems = page.locator('#findingList li');
    await expect(listItems.first()).toBeVisible();
  });
});
