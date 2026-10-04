import { defineConfig } from '@playwright/test'
// Chromium's synthetic test camera stands in for the OBS Virtual Camera.
const media = ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream']
export default defineConfig({
  testDir: './e2e',
  timeout: 45000,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: process.env.AC_AGENT_TEST_URL || 'http://127.0.0.1:8765',
    headless: true,
    permissions: ['camera'],
    launchOptions: process.env.AC_AGENT_BROWSER_PATH
      ? {
          executablePath: process.env.AC_AGENT_BROWSER_PATH,
          args: ['--no-sandbox', '--disable-dev-shm-usage', ...media],
        }
      : { args: media },
  },
})
