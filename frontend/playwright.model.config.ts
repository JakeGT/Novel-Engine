import { defineConfig, devices } from '@playwright/test'

// Browser tests that need a model (`*.model.spec.ts`). The API runs with a local stand-in
// model (`scripts/e2e/fake_model.py`) that gives set answers, so drafting and checking can
// be driven end to end without any provider. Everything else runs in playwright.config.ts,
// where there is no model at all.
const apiPort = Number(process.env.E2E_MODEL_API_PORT ?? 8012)
const webPort = Number(process.env.E2E_MODEL_WEB_PORT ?? 5176)
// The stand-in's own port, fixed so a test can point site search at it.
export const modelPort = Number(process.env.E2E_MODEL_PORT ?? 8013)
const apiUrl = `http://127.0.0.1:${apiPort}`
const webUrl = `http://127.0.0.1:${webPort}`

export default defineConfig({
  testDir: './e2e',
  testMatch: /.*\.model\.spec\.ts/,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: './test-results-model',
  use: {
    baseURL: webUrl,
    trace: 'retain-on-failure',
    channel: process.env.E2E_BROWSER_CHANNEL || undefined,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `uv run --locked python -m scripts.e2e.serve --port ${apiPort} --fake-model`,
      cwd: '..',
      env: { E2E_MODEL_PORT: String(modelPort) },
      url: `${apiUrl}/health/live`,
      reuseExistingServer: false,
      timeout: 180_000,
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
