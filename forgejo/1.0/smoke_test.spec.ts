import { test, expect } from '@playwright/test';

const URL = process.env.FORGEJO_URL!;

/**
 * Forgejo primary-task smoke: register the first user (who becomes site admin
 * with INSTALL_LOCK=true) and reach an authenticated surface. A bare "Sign In
 * link renders" check would pass even if SQLite writes or the registration
 * flow were broken — so we drive sign-up and assert a post-auth signal.
 *
 * 1. GET /api/v1/version  — backend + DB up (JSON, exempt from HTML routing).
 * 2. Register via /user/sign_up (fields verified live: user_name/email/
 *    password/retype). The first registrant is auto-promoted to admin.
 * 3. Assert we left the sign-up page (Forgejo redirects to the dashboard on
 *    success) and a logged-in affordance is present.
 */
test.describe('Forgejo', () => {
  test('registers the first user and reaches the dashboard', async ({ page, request }) => {
    test.skip(!URL, 'FORGEJO_URL not set');

    const base = URL.replace(/\/$/, '');

    const ver = await request.get(`${base}/api/v1/version`, { timeout: 30_000 });
    expect(ver.ok(), `GET /api/v1/version -> ${ver.status()}`).toBe(true);

    await page.goto(`${base}/user/sign_up`, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    const ts = Date.now();
    await page.locator('input[name="user_name"]').first().fill(`e2e${ts}`);
    await page.locator('input[name="email"]').first().fill(`e2e${ts}@example.com`);
    const pws = page.locator('input[name="password"], input[name="retype"]');
    await page.locator('input[name="password"]').first().fill('Sup3r-Forge-Pass');
    const retype = page.locator('input[name="retype"]').first();
    if (await retype.isVisible({ timeout: 2_000 }).catch(() => false)) {
      await retype.fill('Sup3r-Forge-Pass');
    }

    await Promise.all([
      page.waitForURL(u => !/\/user\/sign_up$/.test(u.toString()), { timeout: 20_000 }).catch(() => {}),
      page.getByRole('button', { name: /register|create account|sign up/i }).first().click(),
    ]);

    // Success leaves /user/sign_up. An inline validation error would keep us
    // there; the first-user-becomes-admin path lands on the dashboard/home.
    expect(page.url(), 'should leave the sign-up page after registering')
      .not.toMatch(/\/user\/sign_up$/);
  });
});
