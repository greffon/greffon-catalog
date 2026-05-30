import { test, expect } from '@playwright/test';

const URL = process.env.UPTIME_KUMA_URL!;

/**
 * Uptime Kuma fresh-install happy path: the app serves and the first-run
 * setup screen (create admin account) renders. On a brand-new instance with
 * no admin yet, the root route shows the setup form.
 */
test.describe('Uptime Kuma', () => {
  test('serves the setup screen on a fresh install', async ({ page }) => {
    test.skip(!URL, 'UPTIME_KUMA_URL not set');

    const base = URL.replace(/\/$/, '');
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    // TODO: confirm selector against a live deploy. Uptime Kuma's first-run
    // screen renders a "Create your admin account" heading and username /
    // password inputs; a password input is the most stable landmark.
    const pw = page.locator('input[type="password"]').first();
    await expect(pw).toBeVisible({ timeout: 30_000 });
  });
});
