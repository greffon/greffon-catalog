import { test, expect } from '@playwright/test';

const URL = process.env.VISIO_URL!;

/**
 * Visio (La Suite numerique / Meet) happy path on a fresh self-contained
 * install. Auth is OIDC-only, so an unauthenticated visit hands off to the
 * bundled Keycloak sign-in. We assert the OIDC provider is reachable under
 * /identity and that a usable sign-in surface renders. Demo: demo / demo.
 */
test.describe('Visio', () => {
  test('serves the app and the bundled OIDC sign-in', async ({ page, request }) => {
    test.skip(!URL, 'VISIO_URL not set');
    const base = URL.replace(/\/$/, '');

    const realm = await request.get(
      `${base}/identity/realms/meet/.well-known/openid-configuration`,
      { timeout: 60_000 },
    );
    expect(realm.ok(), `GET /identity/.well-known -> ${realm.status()}`).toBe(true);

    await page.goto(base, { waitUntil: 'networkidle', timeout: 90_000 });
    await expect(page.locator('input[name="username"]').first())
      .toBeVisible({ timeout: 30_000 });
    await expect(page.locator('input[type="password"]').first())
      .toBeVisible({ timeout: 30_000 });
  });
});
