import { test, expect } from '@playwright/test';

const URL = process.env.VIKUNJA_URL!;

/**
 * Vikunja happy path: the single-container app (frontend + API since v0.23)
 * serves and the login/register UI renders. We also hit the API info endpoint
 * which returns JSON regardless of frontend routing.
 */
test.describe('Vikunja', () => {
  test('serves the app and api info', async ({ page, request }) => {
    test.skip(!URL, 'VIKUNJA_URL not set');

    const base = URL.replace(/\/$/, '');

    // /api/v1/info returns JSON on a healthy instance — reliable up-signal.
    const info = await request.get(`${base}/api/v1/info`, { timeout: 30_000 });
    expect(info.ok(), `GET /api/v1/info -> ${info.status()}`).toBe(true);

    // The SPA login surface should render.
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    // TODO: confirm selector against a live deploy. Vikunja's login screen
    // has a password input — stable landmark on a fresh instance.
    const pw = page.locator('input[type="password"]').first();
    await expect(pw).toBeVisible({ timeout: 30_000 });
  });
});
