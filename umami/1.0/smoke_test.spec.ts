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

    // /api/heartbeat returns JSON when the app + DB are healthy. umami (Next.js +
    // prisma migrate on first boot) can take up to ~60s to start, and the proxy
    // 502s until the app is up -- so POLL until ready rather than one-shot (a
    // single request races startup and flakes).
    await expect
      .poll(async () => (await request.get(`${base}/api/heartbeat`)).status(), {
        message: 'umami /api/heartbeat never became 200',
        timeout: 90_000,
        intervals: [2_000],
      })
      .toBe(200);

    // The login page renders (default admin/umami; change on first login).
    await page.goto(`${base}/login`, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    const pw = page.locator('input[type="password"]').first();
    await expect(pw).toBeVisible({ timeout: 30_000 });
  });
});
