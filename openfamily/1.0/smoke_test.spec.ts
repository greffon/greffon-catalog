import { test, expect } from '@playwright/test';

const URL = process.env.OPENFAMILY_URL!;

/**
 * OpenFamily happy path: one container serves the API and the SPA on the same
 * origin (SERVE_CLIENT_DIR mode). We prove the backend booted via /health
 * (JSON, exempt from SPA fallback), then confirm the SPA shell renders its
 * login surface on a fresh instance.
 */
test.describe('OpenFamily', () => {
  test('serves health and the app shell', async ({ page, request }) => {
    test.skip(!URL, 'OPENFAMILY_URL not set');

    const base = URL.replace(/\/$/, '');

    // /health returns 200 JSON once the server has booted and migrated.
    const health = await request.get(`${base}/health`, { timeout: 30_000 });
    expect(health.ok(), `GET /health -> ${health.status()}`).toBe(true);

    // The SPA shell loads: login screen renders on a fresh (no-user) instance.
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    const pw = page.locator('input[type="password"]').first();
    await expect(pw).toBeVisible({ timeout: 30_000 });
  });
});
