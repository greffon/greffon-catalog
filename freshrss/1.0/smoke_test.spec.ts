import { test, expect } from '@playwright/test';

const URL = process.env.FRESHRSS_URL!;

/**
 * FreshRSS fresh-install smoke: a brand-new instance (empty data volume) must
 * serve the install wizard, not a login page. Asserting the FreshRSS wordmark
 * alone is too weak — it appears on both the installer and the login screen,
 * so a reused/already-initialized volume could pass without proving setup
 * works. Assert an installer-specific landmark instead (the wizard title /
 * the /i/ installer route / its language selector).
 */
test.describe('FreshRSS', () => {
  test('serves the install wizard on a fresh instance', async ({ page }) => {
    test.skip(!URL, 'FRESHRSS_URL not set');

    const base = URL.replace(/\/$/, '');
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    // The installer's <title> is "Installation · FreshRSS: step N" and the
    // page exposes a language <select name="language">. Either is a stable,
    // installer-specific landmark absent from the post-setup login page.
    await expect(page).toHaveTitle(/Installation\s*·\s*FreshRSS/i, { timeout: 30_000 });
    await expect(page.locator('select[name="language"]').first()).toBeVisible({ timeout: 15_000 });
  });
});
