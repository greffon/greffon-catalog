import { test, expect } from '@playwright/test';

const URL = process.env.MEMOS_URL!;

/**
 * Memos happy path on a fresh install: the app serves and the first-run
 * sign-up surface renders (the first registrant becomes host/admin). We hit
 * the status API first (JSON, exempt from SPA routing), then confirm the SPA.
 */
test.describe('Memos', () => {
  test('serves the app and status api', async ({ page, request }) => {
    test.skip(!URL, 'MEMOS_URL not set');

    const base = URL.replace(/\/$/, '');

    // Workspace-profile endpoint returns JSON on a healthy instance.
    const status = await request.get(`${base}/api/v1/workspace/profile`, { timeout: 30_000 });
    expect(status.ok(), `GET /api/v1/workspace/profile -> ${status.status()}`).toBe(true);

    // The SPA loads — first run shows the create-host / sign-up form.
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    // TODO: confirm selector against a live deploy. The sign-up form has a
    // password input — the stable landmark on a fresh instance.
    const pw = page.locator('input[type="password"]').first();
    await expect(pw).toBeVisible({ timeout: 30_000 });
  });
});
