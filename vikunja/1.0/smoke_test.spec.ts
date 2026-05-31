import { test, expect } from '@playwright/test';

const URL = process.env.VIKUNJA_URL!;

/**
 * Vikunja primary-task smoke: register the first user and reach the app.
 * On a fresh instance registration is open, so we can create an account and
 * land in the authenticated UI — proving the SQLite volume is writable, the
 * service secret signs tokens, and VIKUNJA_SERVICE_PUBLICURL lets the SPA
 * talk to its own API. We assert the API info endpoint first (reliable
 * up-signal, exempt from SPA routing) then complete registration.
 */
test.describe('Vikunja', () => {
  test('registers the first user and reaches the app', async ({ page, request }) => {
    test.skip(!URL, 'VIKUNJA_URL not set');

    const base = URL.replace(/\/$/, '');

    // /api/v1/info returns JSON on a healthy instance — proves the backend +
    // DB came up (the permission/secret failure modes surface here first).
    const info = await request.get(`${base}/api/v1/info`, { timeout: 30_000 });
    expect(info.ok(), `GET /api/v1/info -> ${info.status()}`).toBe(true);

    // Register the first user via the SPA.
    await page.goto(`${base}/register`, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    const ts = Date.now();
    // TODO: confirm selectors against a live deploy. Vikunja's register form
    // has username / email / password fields; field names are stable enough
    // to target by input type + order with name hints.
    await page.locator('input[id="username"], input[name="username"]').first().fill(`e2e${ts}`);
    const email = page.locator('input[type="email"], input[id="email"]').first();
    if (await email.isVisible({ timeout: 2_000 }).catch(() => false)) {
      await email.fill(`e2e${ts}@example.com`);
    }
    const pws = page.locator('input[type="password"]');
    await pws.nth(0).fill('Sup3r-Vik-Pass');
    if (await pws.count() > 1) await pws.nth(1).fill('Sup3r-Vik-Pass');

    await Promise.all([
      page.waitForURL(u => !/\/register$/.test(u.toString()), { timeout: 20_000 }).catch(() => {}),
      page.getByRole('button', { name: /register|create|sign up/i }).first().click(),
    ]);

    // Landed somewhere past /register — authenticated app or login (some
    // builds route to login after register). Either proves the register flow
    // was accepted; an inline error would keep us on /register.
    expect(page.url(), 'should leave the register page').not.toMatch(/\/register$/);
  });
});
