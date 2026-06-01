import { test, expect } from '@playwright/test';

const URL = process.env.IMMICH_URL!;

/**
 * Immich happy path on a fresh install: the multi-service stack (server + ML +
 * Redis + Postgres) comes up and the first-run admin-registration screen
 * renders. We hit the server-config API first (JSON, exempt from SPA routing)
 * to prove the server + DB + Redis are all healthy, then confirm the SPA.
 *
 * NOTE: first boot is slow (multi-GB image pulls + DB init + ML model), so the
 * timeouts here are generous.
 */
test.describe('Immich', () => {
  test('serves the app and server-config api', async ({ page, request }) => {
    test.skip(!URL, 'IMMICH_URL not set');

    const base = URL.replace(/\/$/, '');

    // Public server-config endpoint returns JSON once the server + DB are up.
    const cfg = await request.get(`${base}/api/server/config`, { timeout: 60_000 });
    expect(cfg.ok(), `GET /api/server/config -> ${cfg.status()}`).toBe(true);

    // The SPA loads — fresh install shows the admin-registration / login form.
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 90_000 });
    // TODO: confirm selector against a live deploy. The register/login form has
    // a password input — the stable landmark on a fresh instance.
    const pw = page.locator('input[type="password"]').first();
    await expect(pw).toBeVisible({ timeout: 45_000 });
  });
});
