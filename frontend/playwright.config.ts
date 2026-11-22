import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.browser.spec.ts',
  timeout: 150000,
  workers: 1,
  retries: 0,
  use: {
    channel: 'msedge',
    headless: true,
    viewport: { width: 1280, height: 840 },
    screenshot: 'only-on-failure',
  },
})
