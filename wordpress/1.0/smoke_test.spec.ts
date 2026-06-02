import { test, expect } from '@playwright/test';

const URL = process.env.WORDPRESS_URL!;

/**
 * WordPress happy path on a fresh install (app + MariaDB): a brand-new
 * instance redirects to the install wizard, where the user creates the admin
 * account. We assert the install screen renders (language picker / "Welcome"
 * setup form) — proving the app reached the DB and is ready to configure.
 * TODO: confirm selectors against a live deploy.
 */
test.describe('WordPress', () => {
  test('serves the install wizard on a fresh instance', async ({ page }) => {
    test.skip(!URL, 'WORDPRESS_URL not set');

    const base = URL.replace(/\/$/, '');
    // Fresh WP redirects / -> /wp-admin/install.php. Hit it directly.
    await page.goto(`${base}/wp-admin/install.php`, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    // The installer renders either the language-select step or the
    // "Welcome"/site-details form. Assert an install-specific landmark (a
    // submit button + the wp install body), not just any page.
    const installLandmark = page
      .getByRole('button', { name: /continue|install wordpress|let's go/i })
      .or(page.locator('form#setup, body.wp-core-ui'))
      .first();
    await expect(installLandmark).toBeVisible({ timeout: 30_000 });
  });
});
