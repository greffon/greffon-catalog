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

    // Keycloak imports the realm on first boot (~30-60s) and refuses
    // connections until it binds, so the gateway 502s /identity until then. The
    // instance reaches the greffer's "running" state (all containers up) before
    // Keycloak finishes importing, so poll the realm discovery doc until it
    // serves, and capture the authorization endpoint for the login check
    // (mirrors the visio greffon's proven gate).
    let authEndpoint = '';
    await expect(async () => {
      const realm = await request.get(
        `${base}/identity/realms/docs/.well-known/openid-configuration`,
        { timeout: 15_000 },
      );
      expect(realm.status(), `GET /identity/.well-known -> ${realm.status()}`).toBe(200);
      authEndpoint = (await realm.json()).authorization_endpoint as string;
    }).toPass({ timeout: 180_000, intervals: [3_000] });

    // Visiting `/` logged-out doesn't reliably render the Keycloak form (the
    // frontend can sit on its own shell / run a silent SSO probe), so drive the
    // authorization endpoint directly. The realm registers {{ instance_url }}/*
    // as a valid redirect, so a standard code-flow request renders the bundled
    // username/password sign-in (self-registration is open; no demo user).
    const authUrl =
      `${authEndpoint}?client_id=docs&response_type=code` +
      `&scope=${encodeURIComponent('openid email')}` +
      `&redirect_uri=${encodeURIComponent(`${base}/api/v1.0/callback/`)}`;
    await page.goto(authUrl, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await expect(page.locator('input[name="username"]').first())
      .toBeVisible({ timeout: 30_000 });
    await expect(page.locator('input[type="password"]').first())
      .toBeVisible({ timeout: 30_000 });
  });
});
