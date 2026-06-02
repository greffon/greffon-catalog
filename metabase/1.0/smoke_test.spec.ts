import { test, expect } from '@playwright/test';

const URL = process.env.METABASE_URL!;

/**
 * Metabase happy path on a fresh install (app + Postgres): the app is healthy
 * and the first-run setup wizard is live. We assert (a) /api/health is ok,
 * (b) the setup-specific API reports has-user-setup=false — proving we reached
 * a fresh, setup-ready Metabase rather than a proxy/error page, and (c) the
 * setup wizard's welcome step renders. The admin account is created in this
 * wizard, so there's no default-credential exposure.
 */
test.describe('Metabase', () => {
  test('serves a fresh, setup-ready instance', async ({ page, request }) => {
    test.skip(!URL, 'METABASE_URL not set');

    const base = URL.replace(/\/$/, '');

    // Metabase can take a while to boot + run migrations (generous timeout).
    const health = await request.get(`${base}/api/health`, { timeout: 90_000 });
    expect(health.ok(), `GET /api/health -> ${health.status()}`).toBe(true);

    // Setup-specific signal: a fresh instance has no user yet. Confirms we hit
    // Metabase (not a proxy error page) and the first-user flow is available.
    const props = await request.get(`${base}/api/session/properties`, { timeout: 30_000 });
    expect(props.ok(), `GET /api/session/properties -> ${props.status()}`).toBe(true);
    expect((await props.json())['has-user-setup'], 'fresh instance should report has-user-setup=false').toBe(false);

    // The setup wizard's welcome step renders behind the proxy.
    await page.goto(`${base}/setup`, { waitUntil: 'networkidle', timeout: 60_000 });
    await expect(
      page.getByText(/welcome to metabase|let's get started|get started/i).first(),
    ).toBeVisible({ timeout: 30_000 });
  });
});
