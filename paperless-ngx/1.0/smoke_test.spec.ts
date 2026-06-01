import { test, expect } from '@playwright/test';

const URL = process.env.PAPERLESS_NGX_URL!;

/**
 * Paperless-ngx happy path: the app (Django + Redis) serves and the login
 * page renders. The PAPERLESS_ADMIN_USER/PASSWORD env create the superuser on
 * first boot, so there's no setup wizard — the root route redirects to login.
 */
test.describe('Paperless-ngx', () => {
  test('serves the login page', async ({ page }) => {
    test.skip(!URL, 'PAPERLESS_NGX_URL not set');

    const base = URL.replace(/\/$/, '');
    await page.goto(`${base}/accounts/login/`, { waitUntil: 'domcontentloaded', timeout: 90_000 });

    // TODO: confirm selector against a live deploy. The login form has a
    // password input — the stable landmark; superuser is env-seeded.
    const pw = page.locator('input[type="password"], input[name="password"]').first();
    await expect(pw).toBeVisible({ timeout: 45_000 });
  });
});
