import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  // Native Windows initializes the disposable DuckDB fixture and Vite cache on
  // the first test run. Keep Linux's defaults while allowing that cold start.
  timeout: process.platform === 'win32' ? 90_000 : 30_000,
  expect: {
    timeout: process.platform === 'win32' ? 15_000 : 5_000,
  },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  // The HTML reporter writes under the Vite project root and keeps the Windows
  // test process alive while the dev server is watching that directory.
  reporter: process.env.CI
    ? [['github']]
    : process.platform === 'win32'
      ? [['list'], ['./e2e/windows-exit-reporter.ts']]
      : [['list']],
  use: {
    baseURL: 'http://127.0.0.1:15173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
