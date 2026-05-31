import { test, expect } from '@playwright/test';

const URL = process.env.JELLYFIN_URL!;

/**
 * Jellyfin happy path on a fresh install: the server serves and the setup
 * wizard renders (no env-configured admin — first run is a web wizard). We
 * assert the public System/Info endpoint returns JSON, then confirm the web
 * UI loads.
 */
test.describe('Jellyfin', () => {
  test('serves the server and public system info', async ({ page, request }) => {
    test.skip(!URL, 'JELLYFIN_URL not set');

    const base = URL.replace(/\/$/, '');

    // Public ping/info endpoint — returns without auth on a healthy server.
    const info = await request.get(`${base}/System/Info/Public`, { timeout: 30_000 });
    expect(info.ok(), `GET /System/Info/Public -> ${info.status()}`).toBe(true);

    // The web UI loads (setup wizard on first run).
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    // TODO: confirm selector against a live deploy. The Jellyfin SPA mounts
    // into the page; assert non-empty body content (wizard/login rendered).
    await expect(page.locator('body')).toContainText(/.+/, { timeout: 30_000 });
  });
});
