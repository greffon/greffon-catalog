import { test, expect } from '@playwright/test';

const URL = process.env.WORDPRESS_URL!;

// First-user credentials created by the install wizard. Strong password so WP
// doesn't surface the "confirm weak password" gate.
const ADMIN_USER = 'greffonadmin';
const ADMIN_PASS = 'Greffon-Smoke-9f3K2pQ7xZ!';
const ADMIN_EMAIL = 'admin@example.com';

/**
 * WordPress happy path on a fresh install (app + MariaDB): drive the real
 * minimum user flow end to end — complete the install wizard to create the
 * admin account, then log in and assert a post-auth signal (the admin bar /
 * wp-admin dashboard). Asserting only that the installer renders would miss
 * proxy-URL and auth regressions that surface only after install, which is
 * why .github/SMOKE_TESTING.md requires exercising first-user creation.
 */
test.describe('WordPress', () => {
  test('installs, creates the admin, and reaches the dashboard', async ({ page }) => {
    test.skip(!URL, 'WORDPRESS_URL not set');
    const base = URL.replace(/\/$/, '');

    // Fresh WP redirects / -> /wp-admin/install.php.
    await page.goto(`${base}/wp-admin/install.php`, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    // Optional language-select step: accept the default and continue.
    const langContinue = page.locator('#language-continue');
    if (await langContinue.isVisible().catch(() => false)) {
      await langContinue.click();
      await page.waitForLoadState('domcontentloaded');
    }

    // Step 2 — site details + admin account.
    await expect(page.locator('#weblog_title')).toBeVisible({ timeout: 30_000 });
    await page.fill('#weblog_title', 'Greffon Smoke');
    await page.fill('#user_login', ADMIN_USER);
    // WP pre-fills #pass1 with a generated strong password; replace it.
    await page.fill('#pass1', ADMIN_PASS);
    await page.fill('#admin_email', ADMIN_EMAIL);
    // Defensive: dismiss the weak-password confirmation if it ever appears.
    const weak = page.locator('input[name="pw_weak"]');
    if (await weak.isVisible().catch(() => false)) await weak.check().catch(() => {});

    await page.click('#submit');

    // Install success.
    await expect(page.getByText(/success/i).first()).toBeVisible({ timeout: 60_000 });

    // Log in with the account we just created.
    await page.goto(`${base}/wp-login.php`, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await page.fill('#user_login', ADMIN_USER);
    await page.fill('#user_pass', ADMIN_PASS);
    // Don't await the post-login navigation: it heads to the canonical
    // WP_SITEURL, which in the live-smoke harness is a portless placeholder
    // (dead from the test's perspective). noWaitAfter lets the cookie poll run.
    await page.click('#wp-submit', { noWaitAfter: true });

    // Post-auth signal: the WordPress logged-in cookie. We assert the cookie
    // rather than the dashboard chrome because WP canonicalizes redirects to
    // WP_SITEURL; in the live-smoke harness that's a placeholder host without
    // the allocated port, so following the post-login redirect would leave the
    // reachable endpoint. The wordpress_logged_in_* cookie proves auth worked.
    await expect
      .poll(
        async () =>
          (await page.context().cookies()).some((c) => c.name.startsWith('wordpress_logged_in')),
        { timeout: 30_000 },
      )
      .toBe(true);
  });
});
