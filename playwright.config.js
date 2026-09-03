// @ts-check
const { defineConfig } = require('@playwright/test');

// 本地 uvicorn 启动命令（托管 venv 提供完整依赖；QC_PY 环境变量可覆盖）
const { homedir } = require('os');
const PY = process.env.QC_PY || require('path').join(homedir(), '.workbuddy/binaries/python/envs/default/bin/python3');
// E2E 独立数据目录（避免污染本地样本库/账号）；每次启动前清理确保"首个账号"免鉴权
const E2E_APPDATA = '/tmp/qc_e2e_appdata';

module.exports = defineConfig({
  testDir: './tests/e2e',
  timeout: 60000,
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:8500',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: {
    command: `rm -rf ${E2E_APPDATA} && QC_APPDATA=${E2E_APPDATA} QC_DB_OVERRIDE=${E2E_APPDATA}/e2e.db ${PY} -m uvicorn server.main:app --host 127.0.0.1 --port 8500`,
    url: 'http://127.0.0.1:8500/api/v1/health',
    reuseExistingServer: false,
    timeout: 120000,
  },
});
