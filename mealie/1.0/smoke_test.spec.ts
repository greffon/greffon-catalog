import { test, expect } from '@playwright/test';

const URL = process.env.MEALIE_URL!;

/**
 * Mealie happy path: the app serves and renders. We hit the API app-info
 * endpoint first (JSON, exempt from SPA routing) to prove the backend booted,
 * then confirm the SPA shell loads.
 */
test.describe('Mealie', () => {
  test('serves the app and api info', async ({ page, request }) => {
    test.skip(!URL, 'MEALIE_URL not set');

    const base = URL.replace(/\/$/, '');

    // /api/app/about returns JSON (version, etc.) on a healthy instance.
    const about = await request.get(`${base}/api/app/about`, { timeout: 30_000 });
    expect(about.ok(), `GET /api/app/about -> ${about.status()}`).toBe(true);

    // The SPA shell loads — login/landing surface renders.
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    // TODO: confirm selector against a live deploy. Mealie's login screen has
    // a password input; it's the stable landmark on a fresh instance.
    const pw = page.locator('input[type="password"]').first();
    await expect(pw).toBeVisible({ timeout: 30_000 });
  });
});
