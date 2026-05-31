import { test, expect } from '@playwright/test';

const URL = process.env.UPTIME_KUMA_URL!;

/**
 * Uptime Kuma fresh-install happy path. The configured :2 image shows a
 * database-selection wizard FIRST on a blank /app/data volume (SQLite was the
 * implicit default only before v2.0.0), and only after a DB is initialized
 * does the create-admin-account form appear. So we assert that the Vue SPA
 * mounted and the first-run setup flow is reachable — accepting either the
 * v2 database wizard or the (post-DB) admin-account form — rather than
 * waiting for a password field that isn't present on the very first screen.
 */
test.describe('Uptime Kuma', () => {
  test('serves the first-run setup flow', async ({ page }) => {
    test.skip(!URL, 'UPTIME_KUMA_URL not set');

    const base = URL.replace(/\/$/, '');
    await page.goto(base, { waitUntil: 'networkidle', timeout: 60_000 });

    // The Vue app mounts into <div id="app">. Assert it rendered content.
    const app = page.locator('#app');
    await expect(app).toBeVisible({ timeout: 30_000 });

    // Accept either first-run surface:
    //   - v2 database-selection wizard (e.g. "SQLite" / "Embedded MariaDB"
    //     choice, "Setup Database" heading), or
    //   - the create-admin-account form (a password input).
    // TODO: confirm exact copy against a live deploy.
    const dbWizard = page.getByText(/database|sqlite|mariadb|setup/i).first();
    const adminForm = page.locator('input[type="password"]').first();
    await expect(dbWizard.or(adminForm)).toBeVisible({ timeout: 30_000 });
  });
});
