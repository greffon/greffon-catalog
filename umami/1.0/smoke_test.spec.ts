import { test, expect } from '@playwright/test';

const URL = process.env.UMAMI_URL!;

/**
 * Umami happy path: the app (Next.js + Postgres) serves and the login page
 * renders. We hit the heartbeat API first (JSON, proves app + Postgres are
 * up — the multi-service failure surfaces here), then confirm the login UI.
 */
test.describe('Umami', () => {
  test('serves the app and heartbeat', async ({ page, request }) => {
    test.skip(!URL, 'UMAMI_URL not set');

    const base = URL.replace(/\/$/, '');

    // /api/heartbeat returns JSON when the app + DB are healthy.
    const beat = await request.get(`${base}/api/heartbeat`, { timeout: 30_000 });
    expect(beat.ok(), `GET /api/heartbeat -> ${beat.status()}`).toBe(true);

    // The login page renders (default admin/umami; change on first login).
    await page.goto(`${base}/login`, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    const pw = page.locator('input[type="password"]').first();
    await expect(pw).toBeVisible({ timeout: 30_000 });
  });
});
