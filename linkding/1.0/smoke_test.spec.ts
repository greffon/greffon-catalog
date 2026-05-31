import { test, expect } from '@playwright/test';

const URL = process.env.LINKDING_URL!;

/**
 * Linkding smoke: the app serves and the login page renders. We keep this
 * deliberately light — it needs no injected secret and no proxy-rewritten
 * origin, so it's honest about what the CI harness can actually exercise.
 * The deeper "log in + add a bookmark" flow depends on the configured
 * superuser password and the public CSRF origin, which are validated by a
 * live graft-test rather than asserted here.
 */
test.describe('Linkding', () => {
  test('serves the login page', async ({ page, request }) => {
    test.skip(!URL, 'LINKDING_URL not set');

    const base = URL.replace(/\/$/, '');

    // Health endpoint is the reliable "it's up" signal (JSON, no auth).
    const health = await request.get(`${base}/health`, { timeout: 30_000 });
    expect(health.ok(), `GET /health -> ${health.status()}`).toBe(true);

    // The login page renders its form — the app booted and serves HTML.
    await page.goto(`${base}/login/`, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await expect(page).toHaveTitle(/Login - Linkding/i, { timeout: 30_000 });
    await expect(page.locator('input[type="password"]').first()).toBeVisible({ timeout: 30_000 });
  });
});
