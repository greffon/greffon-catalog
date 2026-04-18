import { test, expect, request as pwRequest } from '@playwright/test';

const URL = process.env.NEXTCLOUD_URL!;

/**
 * Nextcloud full happy path:
 *   - Container's env-var auto-install created admin/Admin123! on fresh volume
 *   - NEXTCLOUD_TRUSTED_DOMAINS resolved via {{ instance_host }} template
 *   - Login succeeds and lands on the dashboard
 */
test.describe('Nextcloud', () => {
  test('admin logs in, lands on dashboard', async ({ page }) => {
    test.skip(!URL, 'NEXTCLOUD_URL not set');

    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    // Follow redirect to /login if present.
    await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});

    // Login form should be present (admin already provisioned).
    const user = page
      .locator('input[name="user"], input#user, input[autocomplete="username"]')
      .first();
    await expect(user).toBeVisible({ timeout: 30_000 });
    await user.fill('admin');
    await page.locator('input[type="password"]').first().fill('Admin123!');
    await page.locator('button[type="submit"]').first().click();

    await page.waitForLoadState('domcontentloaded', { timeout: 30_000 }).catch(() => {});

    // Dismiss first-run welcome modal if present.
    for (let i = 0; i < 3; i++) {
      const dismiss = page.getByRole('button', { name: /close|dismiss|skip|not now/i }).first();
      if (await dismiss.isVisible({ timeout: 1_000 }).catch(() => false)) {
        await dismiss.click().catch(() => {});
        await page.waitForTimeout(300);
      } else break;
    }

    // Post-login: not on /login, and header renders.
    expect(page.url()).not.toMatch(/\/login/);
    const header = page.locator('#header, header, [role="banner"]').first();
    await expect(header).toBeVisible({ timeout: 30_000 });
  });

  test('admin can create a folder via WebDAV', async () => {
    test.skip(!URL, 'NEXTCLOUD_URL not set');

    // Use the WebDAV API directly with an APIRequestContext that doesn't
    // carry a browser cookie jar — simpler, stable, and proves the admin
    // user Nextcloud's env-var auto-install provisioned actually exists
    // and can write.
    const ctx = await pwRequest.newContext({
      ignoreHTTPSErrors: true,
      extraHTTPHeaders: {
        Authorization: 'Basic ' + Buffer.from('admin:Admin123!').toString('base64'),
      },
    });
    try {
      const folderName = `MyDocs-${Date.now()}`;
      const resp = await ctx.fetch(`${URL}/remote.php/dav/files/admin/${folderName}`, {
        method: 'MKCOL',
      });
      expect(resp.status(), `MKCOL /remote.php/dav/files/admin/${folderName}`).toBe(201);
    } finally {
      await ctx.dispose();
    }
  });
});
