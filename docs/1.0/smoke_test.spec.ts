import { test, expect } from '@playwright/test';

const URL = process.env.DOCS_URL!;

/**
 * Docs (La Suite numerique) happy path on a fresh, self-contained install.
 *
 * Docs authenticates ONLY via OIDC, so an unauthenticated visit to the app
 * origin must hand off to the bundled Keycloak login screen (path-mounted under
 * /identity). We assert that the sign-in surface actually renders — a username
 * field, a password field, and a submit control — rather than just a non-empty
 * body (which a crash page or the greffer's TLS proxy would also satisfy).
 *
 * Demo credentials provisioned by the bundled realm: demo / demo.
 */
test.describe('Docs', () => {
  test('hands off to the bundled OIDC sign-in screen', async ({ page, request }) => {
    test.skip(!URL, 'DOCS_URL not set');

    const base = URL.replace(/\/$/, '');

    // The embedded identity provider must be reachable under /identity.
    const realm = await request.get(
      `${base}/identity/realms/docs/.well-known/openid-configuration`,
      { timeout: 60_000 },
    );
    expect(realm.ok(), `GET /identity/.well-known -> ${realm.status()}`).toBe(true);

    // Visiting the app while logged out must land on a usable login form.
    await page.goto(base, { waitUntil: 'networkidle', timeout: 90_000 });
    await expect(page.locator('input[name="username"]').first())
      .toBeVisible({ timeout: 30_000 });
    await expect(page.locator('input[type="password"]').first())
      .toBeVisible({ timeout: 30_000 });
    await expect(page.locator('input[type="submit"], button[type="submit"]').first())
      .toBeVisible({ timeout: 30_000 });
  });
});
