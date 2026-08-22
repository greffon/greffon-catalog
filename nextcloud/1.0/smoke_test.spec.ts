import { test, expect, request as pwRequest } from '@playwright/test';

const URL = process.env.NEXTCLOUD_URL!;

/**
 * Nextcloud full happy path:
 *   - Container's env-var auto-install created admin/Admin123! on fresh volume
 *   - NEXTCLOUD_TRUSTED_DOMAINS resolved via {{ instance_host }} template
 *   - Login succeeds and lands on the dashboard
 */
/**
 * Wait for Nextcloud to finish INSTALLING, not merely to be running.
 *
 * The smoke harness gates on the greffer reporting the containers `running`
 * (ci_greffer_smoke.py), which happens seconds after start. Nextcloud's env-var
 * auto-install then runs for tens of seconds more, and until it finishes the app
 * serves the setup wizard: no login form, and WebDAV answers 400. Both tests in
 * this file were racing that install.
 *
 * `/status.php` is the app's own signal. Note the check is `installed === true`
 * on the PARSED json, not a substring: the body of an uninstalled instance is
 * `{"installed":false,...}`, so a substring test for `installed` matches the very
 * state it is supposed to exclude.
 */
async function waitUntilInstalled() {
  const ctx = await pwRequest.newContext({ ignoreHTTPSErrors: true });
  // Carry the last thing we actually saw into the failure message. A bare
  // "never became ready" says nothing about WHY, and this instance installs in
  // ~39s locally with the identical images and env, so when CI disagrees the
  // response body is the evidence that distinguishes "still installing" from
  // "not reachable" from "reachable but erroring".
  let last = 'no response';
  try {
    await expect
      .poll(
        async () => {
          const r = await ctx.get(`${URL}/status.php`, { timeout: 15_000 }).catch((e) => {
            last = `request failed: ${e}`;
            return null;
          });
          if (!r) return false;
          const body = await r.text().catch(() => '<unreadable>');
          last = `HTTP ${r.status()} ${body.slice(0, 200)}`;
          if (!r.ok()) return false;
          try {
            return (JSON.parse(body) as { installed?: boolean }).installed === true;
          } catch {
            return false;
          }
        },
        {
          message: `Nextcloud never reported installed:true; last /status.php was ${last}`,
          timeout: 180_000,
          intervals: [2_000],
        },
      )
      .toBe(true);
  } catch (e) {
    throw new Error(`Nextcloud never reported installed:true. Last /status.php: ${last}`);
  } finally {
    await ctx.dispose();
  }
}

test.describe('Nextcloud', () => {
  test.beforeEach(async () => {
    test.skip(!URL, 'NEXTCLOUD_URL not set');
    await waitUntilInstalled();
  });
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
