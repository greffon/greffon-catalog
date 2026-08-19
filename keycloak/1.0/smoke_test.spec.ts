import { test, expect } from '@playwright/test';

const URL = process.env.KEYCLOAK_URL!;

test.use({ video: 'on' });

/**
 * Keycloak minimal use case. A fresh instance is a usable OIDC provider:
 *
 *   1. Wait through cold start: a Quarkus augmentation pass and the Postgres
 *      schema migration run before the first request is served (~20s measured,
 *      substantially longer on a cold image pull).
 *   2. Fetch the master realm's OIDC discovery document. On a fresh install it
 *      is public, needs no auth, and is the exact document every federated app
 *      reads to find the authorization, token and JWKS endpoints. If it serves,
 *      the identity provider is genuinely up.
 *
 * Why discovery JSON and not the admin console: Keycloak builds every absolute
 * URL (issuer, redirects, console assets) from KC_HOSTNAME. In the local-greffer
 * probe the assigned host port need not match the configured instance URL, so
 * console assets can point somewhere unreachable while the server itself is
 * perfectly healthy. The discovery endpoint answers on whatever host it was
 * asked, which makes it the honest signal.
 *
 * The issuer assertion is load-bearing, not decoration: it is the one output
 * that proves KC_HOSTNAME was templated from instance_url and that Keycloak
 * accepted it. If that env var were dropped or left as an internal service
 * name, this returns an http:// or hostname-less issuer and every app
 * federating to this instance would fail its token validation.
 */
test.describe('Keycloak', () => {
  test('fresh install, master realm serves OIDC discovery over https', async ({ page }) => {
    expect(URL, 'KEYCLOAK_URL not set').toBeTruthy();

    const discovery = `${URL.replace(/\/$/, '')}/realms/master/.well-known/openid-configuration`;

    await expect(async () => {
      const resp = await page.goto(discovery, {
        waitUntil: 'domcontentloaded',
        timeout: 15_000,
      });
      expect(resp?.status(), 'discovery endpoint not yet 200').toBe(200);

      const body = await page.locator('body').innerText();
      const doc = JSON.parse(body);

      expect(doc.issuer, 'issuer must be the public https origin').toMatch(
        /^https:\/\/.+\/realms\/master$/,
      );
      expect(doc.authorization_endpoint, 'missing authorization_endpoint').toContain(
        '/protocol/openid-connect/auth',
      );
      expect(doc.jwks_uri, 'missing jwks_uri').toContain('/protocol/openid-connect/certs');
    // Budget stays under playwright.config.ts's 180s per-test timeout, which
    // would otherwise kill the test before toPass gave up.
    }).toPass({ timeout: 150_000, intervals: [5_000] });
  });
});
