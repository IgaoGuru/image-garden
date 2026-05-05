import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'http://127.0.0.1:5177',
  },
  webServer: {
    command: 'pnpm dev --port 5177 --strictPort',
    url: 'http://127.0.0.1:5177/demo/',
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
