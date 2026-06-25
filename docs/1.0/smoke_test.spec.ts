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
 * Auth is via the bundled realm; self-registration is open (no demo user).
 */
test.describe('Docs', () => {
  test('hands off to the bundled OIDC sign-in screen', async ({ page, request }) => {
    test.skip(!URL, 'DOCS_URL not set');

    const base = URL.replace(/\/$/, '');

    // The embedded Keycloak imports its realm on boot (~30-60s) and the gateway
    // 502s until it's up, so the greffer reporting "running" (containers up)
    // races realm readiness. A single request does NOT retry on 502 -- POLL the
    // realm's well-known until it returns 200 (the realm is imported + served).
    await expect
      .poll(
        async () =>
          (
            await request.get(
              `${base}/identity/realms/docs/.well-known/openid-configuration`,
            )
          ).status(),
        {
          message: 'docs /identity realm well-known never became 200',
          timeout: 120_000,
          intervals: [3_000],
        },
      )
      .toBe(200);

    // A 200 well-known proves Keycloak is up, but the Docs frontend+backend
    // (~10 services behind the gateway) can still be warming up, so the app ->
    // OIDC redirect transiently 502s / lands on a blank shell. Reload-poll the
    // app origin until the bundled sign-in form actually renders its username
    // field, rather than asserting it on a single (racy) navigation.
    await expect
      .poll(
        async () => {
          await page
            .goto(base, { waitUntil: 'domcontentloaded', timeout: 90_000 })
            .catch(() => {});
          return page
            .locator('input[name="username"]')
            .first()
            .isVisible({ timeout: 2_000 })
            .catch(() => false);
        },
        {
          message: 'docs never handed off to a rendered OIDC sign-in form',
          timeout: 150_000,
          intervals: [3_000],
        },
      )
      .toBe(true);

    // The sign-in surface is usable: password + submit are present too.
    await expect(page.locator('input[type="password"]').first())
      .toBeVisible({ timeout: 30_000 });
    await expect(page.locator('input[type="submit"], button[type="submit"]').first())
      .toBeVisible({ timeout: 30_000 });
  });
});
