import { test, expect } from '@playwright/test';

const URL = process.env.LINKDING_URL!;
// The catalog imports the configured admin password into this env var at
// deploy time (see metadata.json LD_SUPERUSER_PASSWORD). Default mirrors the
// value the CI harness injects; a real deploy passes the user's chosen one.
const PASSWORD = process.env.LINKDING_ADMIN_PASSWORD || 'Test-Pass-1234';
const USERNAME = process.env.LINKDING_ADMIN_USERNAME || 'admin';

/**
 * Linkding primary-task smoke: log in with the configured superuser and reach
 * the bookmarks app. A bare "login form renders" check would pass even when
 * LD_SUPERUSER_PASSWORD wasn't injected or LD_CSRF_TRUSTED_ORIGINS is wrong
 * (login POST would fail) — so we actually authenticate and assert a
 * post-login landmark.
 */
test.describe('Linkding', () => {
  test('logs in and reaches the bookmarks app', async ({ page }) => {
    test.skip(!URL, 'LINKDING_URL not set');

    const base = URL.replace(/\/$/, '');
    await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await expect(page).toHaveTitle(/Login - Linkding/i, { timeout: 30_000 });

    await page.locator('input[name="username"], #id_username').first().fill(USERNAME);
    await page.locator('input[type="password"], #id_password').first().fill(PASSWORD);
    await Promise.all([
      page.waitForURL(/\/bookmarks/, { timeout: 20_000 }).catch(() => {}),
      page.getByRole('button', { name: /login/i }).first().click(),
    ]);

    // Authenticated landmark — proves credentials + CSRF origin actually work,
    // not just that a form rendered. "Add bookmark" is present on /bookmarks.
    await expect(page.getByText(/add bookmark/i).first()).toBeVisible({ timeout: 20_000 });
    expect(page.url(), 'should be on the bookmarks app').toMatch(/\/bookmarks/);
  });
});
