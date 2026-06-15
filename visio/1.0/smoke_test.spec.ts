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

    // Keycloak imports the realm on first boot (~30-60s) and refuses
    // connections until it binds, so the gateway 502s /identity until then.
    // The instance reaches the greffer's "running" state (all containers up)
    // before Keycloak finishes importing, so poll the realm discovery doc
    // until it serves, mirroring the ghost greffon's cold-start gate. Capture
    // the authorization endpoint from the discovery doc for the login check.
    let authEndpoint = '';
    await expect(async () => {
      const realm = await request.get(
        `${base}/identity/realms/meet/.well-known/openid-configuration`,
        { timeout: 15_000 },
      );
      expect(realm.status(), `GET /identity/.well-known -> ${realm.status()}`).toBe(200);
      authEndpoint = (await realm.json()).authorization_endpoint as string;
    }).toPass({ timeout: 180_000, intervals: [3_000] });

    // The Meet frontend runs a silent (prompt=none) SSO probe on load and keeps
    // the user on its own landing page instead of redirecting to the IdP (unlike
    // Docs), so visiting `/` never renders the Keycloak form. Drive the
    // authorization endpoint directly: the realm registers {{ instance_url }}/*
    // as a valid redirect, so a standard code-flow request renders the bundled
    // username/password sign-in (demo / demo).
    const authUrl =
      `${authEndpoint}?client_id=meet&response_type=code` +
      `&scope=${encodeURIComponent('openid email')}` +
      `&redirect_uri=${encodeURIComponent(`${base}/api/v1.0/callback/`)}`;
    await page.goto(authUrl, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await expect(page.locator('input[name="username"]').first())
      .toBeVisible({ timeout: 30_000 });
    await expect(page.locator('input[type="password"]').first())
      .toBeVisible({ timeout: 30_000 });
  });
});
