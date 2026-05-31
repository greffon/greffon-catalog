import { test, expect } from '@playwright/test';

const URL = process.env.LINKDING_URL!;

/**
 * Linkding happy path: the app serves and the login page renders. linkding
 * creates the configured superuser on first boot, so the root route redirects
 * to /login with a username/password form.
 */
test.describe('Linkding', () => {
  test('serves the login page', async ({ page }) => {
    test.skip(!URL, 'LINKDING_URL not set');

    const base = URL.replace(/\/$/, '');
    await page.goto(`${base}/login`, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    // TODO: confirm selector against a live deploy. linkding's login form has
    // a password input — the most stable landmark on a fresh instance.
    const pw = page.locator('input[type="password"]').first();
    await expect(pw).toBeVisible({ timeout: 30_000 });
  });
});
