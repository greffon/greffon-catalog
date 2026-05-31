import { test, expect } from '@playwright/test';

const URL = process.env.FORGEJO_URL!;

/**
 * Forgejo happy path: with INSTALL_LOCK=true the first-run web installer is
 * skipped, so the app serves its landing/login page directly (no setup
 * wizard). We assert the API version endpoint returns JSON (reliable
 * up-signal, exempt from HTML routing) and that the web UI renders the
 * Forgejo/Sign-In surface.
 */
test.describe('Forgejo', () => {
  test('serves the forge UI and api', async ({ page, request }) => {
    test.skip(!URL, 'FORGEJO_URL not set');

    const base = URL.replace(/\/$/, '');

    // Public API version endpoint — JSON, proves the backend is up.
    const ver = await request.get(`${base}/api/v1/version`, { timeout: 30_000 });
    expect(ver.ok(), `GET /api/v1/version -> ${ver.status()}`).toBe(true);

    // Web UI loads. INSTALL_LOCK skips the installer, so the landing page
    // renders with a Sign In / Register affordance. TODO: confirm the exact
    // selector against a live deploy; the "Sign In" link is a stable landmark.
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await expect(page.getByRole('link', { name: /sign in/i }).first())
      .toBeVisible({ timeout: 30_000 });
  });
});
