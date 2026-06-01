import { test, expect } from '@playwright/test';

const URL = process.env.GRAFANA_URL!;

/**
 * Grafana happy path: the app serves and the login page renders. We assert
 * the health API returns JSON (proves the server + SQLite are up), then
 * confirm the login UI.
 */
test.describe('Grafana', () => {
  test('serves the app and health api', async ({ page, request }) => {
    test.skip(!URL, 'GRAFANA_URL not set');

    const base = URL.replace(/\/$/, '');

    // /api/health returns {"database":"ok",...} when the server + DB are up.
    const health = await request.get(`${base}/api/health`, { timeout: 30_000 });
    expect(health.ok(), `GET /api/health -> ${health.status()}`).toBe(true);

    // The login page renders (Grafana redirects unauthenticated users here).
    await page.goto(`${base}/login`, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    const pw = page.locator('input[type="password"], input[name="password"]').first();
    await expect(pw).toBeVisible({ timeout: 30_000 });
  });
});
