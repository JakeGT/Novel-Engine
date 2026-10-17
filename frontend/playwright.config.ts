import { defineConfig, devices } from '@playwright/test'

// End-to-end tests drive the real UI against a real backend on disposable state:
// `scripts/e2e/serve.py` creates and migrates a throwaway PostgreSQL database and
// serves the API in local auth mode; Vite serves the app and proxies to it.
const apiPort = Number(process.env.E2E_API_PORT ?? 8010)
const webPort = Number(process.env.E2E_WEB_PORT ?? 5174)
const apiUrl = `http://127.0.0.1:${apiPort}`
const webUrl = `http://127.0.0.1:${webPort}`

export default defineConfig({
  testDir: './e2e',
  // Tests that need a model run in playwright.model.config.ts.
  testIgnore: /.*\.model\.spec\.ts/,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: './test-results',
  use: {
    baseURL: webUrl,
    trace: 'retain-on-failure',
    // Set E2E_BROWSER_CHANNEL=chrome to use an installed Chrome instead of the
    // Playwright-managed Chromium (`make frontend-e2e-install`).
    channel: process.env.E2E_BROWSER_CHANNEL || undefined,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `uv run --locked python -m scripts.e2e.serve --port ${apiPort}`,
      cwd: '..',
      url: `${apiUrl}/health/live`,
      reuseExistingServer: false,
      timeout: 180_000,
      // SIGTERM lets the launcher drop its disposable database.
      gracefulShutdown: { signal: 'SIGTERM', timeout: 20_000 },
    },
    {
      command: `node node_modules/vite/bin/vite.js --host 127.0.0.1 --port ${webPort} --strictPort`,
      url: webUrl,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { VITE_API_PROXY_TARGET: apiUrl },
    },
  ],
})
