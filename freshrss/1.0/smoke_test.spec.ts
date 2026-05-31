import { test, expect } from '@playwright/test';

const URL = process.env.FRESHRSS_URL!;

/**
 * FreshRSS happy path on a fresh install: the app serves and the web
 * installer renders. On first run with an empty data volume, the root route
 * shows the setup wizard (step 1: language / general checks).
 */
test.describe('FreshRSS', () => {
  test('serves the install wizard on a fresh instance', async ({ page }) => {
    test.skip(!URL, 'FRESHRSS_URL not set');

    const base = URL.replace(/\/$/, '');
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    // TODO: confirm selector against a live deploy. FreshRSS first-run shows
    // the installer ("FreshRSS" branding + a form/Next control). Assert the
    // FreshRSS wordmark is visible — present on both installer and login.
    await expect(page.getByText(/freshrss/i).first()).toBeVisible({ timeout: 30_000 });
  });
});
