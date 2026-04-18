import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config for catalog smoke tests.
 *
 * Each greffon's `smoke_test.spec.ts` is discovered from the catalog root
 * (one level up from .github/smoke). The CI matrix sets the `<GREFFON>_URL`
 * env var (e.g. NEXTCLOUD_URL) before launching the spec.
 */
export default defineConfig({
  testDir: '.',
  testMatch: '**/smoke_test.spec.ts',
  fullyParallel: false,  // runner runs one greffon at a time per matrix slot
  workers: 1,
  timeout: 180_000,
  retries: 1,
  reporter: [['list'], ['html', { outputFolder: '.playwright/report', open: 'never' }]],
  outputDir: '.playwright/results',
  use: {
    ignoreHTTPSErrors: true,
    trace: 'on',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
    viewport: { width: 1440, height: 900 },
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
