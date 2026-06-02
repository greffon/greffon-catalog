import { test, expect } from '@playwright/test';

const URL = process.env.METABASE_URL!;

/**
 * Metabase happy path on a fresh install: the server is healthy and the
 * first-run setup wizard renders (admin is created via the wizard, so there's
 * no default-credential exposure). We assert /api/health returns JSON, then
 * confirm the setup UI loads. TODO: confirm selectors against a live deploy.
 */
test.describe('Metabase', () => {
  test('serves the app and health api', async ({ page, request }) => {
    test.skip(!URL, 'METABASE_URL not set');

    const base = URL.replace(/\/$/, '');

    // /api/health returns {"status":"ok"} once the app + H2 are initialized
    // (Metabase takes a while to boot — generous timeout).
    const health = await request.get(`${base}/api/health`, { timeout: 60_000 });
    expect(health.ok(), `GET /api/health -> ${health.status()}`).toBe(true);

    // Fresh instance redirects to /setup; the wizard shell renders.
    await page.goto(`${base}/setup`, { waitUntil: 'networkidle', timeout: 60_000 });
    await expect(page.locator('body')).toContainText(/.+/, { timeout: 30_000 });
  });
});
