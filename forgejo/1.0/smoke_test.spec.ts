import { test, expect } from '@playwright/test';

const URL = process.env.FORGEJO_URL!;

/**
 * Forgejo primary-task smoke: register the first user (who becomes site admin
 * with INSTALL_LOCK=true) and prove they are authenticated.
 *
 * 1. GET /api/v1/version — backend + DB up (JSON, exempt from HTML routing).
 * 2. Register via /user/sign_up (fields verified live: user_name/email/
 *    password/retype).
 * 3. Authenticated assertion: GET /api/v1/user with the new credentials
 *    (basic auth) returns the user object only if login actually works —
 *    a confirmed logged-in signal, not just "left the sign-up page". Also
 *    asserts is_admin, confirming the first-registrant-becomes-admin path.
 */
test.describe('Forgejo', () => {
  test('registers the first user and authenticates as admin', async ({ page, request }) => {
    test.skip(!URL, 'FORGEJO_URL not set');

    const base = URL.replace(/\/$/, '');
    const ts = Date.now();
    const username = `e2e${ts}`;
    const password = 'Sup3r-Forge-Pass';

    const ver = await request.get(`${base}/api/v1/version`, { timeout: 30_000 });
    expect(ver.ok(), `GET /api/v1/version -> ${ver.status()}`).toBe(true);

    // Register the first user via the web form.
    await page.goto(`${base}/user/sign_up`, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await page.locator('input[name="user_name"]').first().fill(username);
    await page.locator('input[name="email"]').first().fill(`${username}@example.com`);
    await page.locator('input[name="password"]').first().fill(password);
    const retype = page.locator('input[name="retype"]').first();
    if (await retype.isVisible({ timeout: 2_000 }).catch(() => false)) {
      await retype.fill(password);
    }
    await Promise.all([
      page.waitForURL(u => !/\/user\/sign_up$/.test(u.toString()), { timeout: 20_000 }).catch(() => {}),
      page.getByRole('button', { name: /register|create account|sign up/i }).first().click(),
    ]);

    // Authenticated signal: this endpoint returns the user ONLY when the
    // credentials actually log in. A redirect to /login or /activate (the
    // false-pass cases) would not produce a valid /api/v1/user response.
    const me = await request.get(`${base}/api/v1/user`, {
      headers: { Authorization: 'Basic ' + Buffer.from(`${username}:${password}`).toString('base64') },
      timeout: 30_000,
    });
    expect(me.ok(), `authenticated GET /api/v1/user -> ${me.status()}`).toBe(true);
    const body = await me.json();
    expect(body.login, 'authenticated user login').toBe(username);
    // First registrant is auto-promoted to site admin.
    expect(body.is_admin, 'first user should be site admin').toBe(true);
  });
});
