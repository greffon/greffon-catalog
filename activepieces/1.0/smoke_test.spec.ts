import { test, expect } from '@playwright/test';

const URL = process.env.ACTIVEPIECES_URL!;

/**
 * Activepieces happy path: the app (app + worker in one container, backed by
 * Postgres + Redis) serves and the first-run sign-up surface renders. On a
 * fresh instance the root route shows the create-admin form. We hit a backend
 * API endpoint first (JSON, exempt from SPA routing) to prove the app +
 * Postgres + Redis all came up, then confirm the SPA shell.
 */
test.describe('Activepieces', () => {
  test('serves the app and backend api', async ({ page, request }) => {
    test.skip(!URL, 'ACTIVEPIECES_URL not set');

    const base = URL.replace(/\/$/, '');

    // Backend health/flags endpoint returns JSON only when the app booted and
    // connected to Postgres + Redis — the multi-service failure modes surface
    // here first. TODO: confirm the exact path against a live deploy;
    // /api/v1/flags is the documented public config endpoint.
    const flags = await request.get(`${base}/api/v1/flags`, { timeout: 30_000 });
    expect(flags.ok(), `GET /api/v1/flags -> ${flags.status()}`).toBe(true);

    // The SPA shell loads — first run renders the create-admin / sign-up form.
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    // TODO: confirm selector against a live deploy. The sign-up form exposes
    // an email + password input; a password input is the stable landmark.
    const pw = page.locator('input[type="password"]').first();
    await expect(pw).toBeVisible({ timeout: 45_000 });
  });
});
