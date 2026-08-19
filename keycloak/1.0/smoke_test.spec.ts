import { test, expect } from '@playwright/test';

const URL = process.env.KEYCLOAK_URL!;

/**
 * Keycloak minimal use case. A fresh instance is a usable OIDC provider AND has
 * a working admin account:
 *
 *   1. Wait through cold start: a Quarkus augmentation pass and the Postgres
 *      schema migration run before the first request is served: budget 30-40s on
 *      an unloaded machine, minutes on a contended one, and longer still on a
 *      cold image pull. That is what the toPass budget below is sized for.
 *   2. Fetch the master realm's OIDC discovery document and assert the issuer is
 *      EXACTLY the instance origin. This is the document every federated app
 *      reads to find the authorization, token and JWKS endpoints.
 *   3. Exchange the bootstrap admin credentials for a token via `admin-cli`.
 *
 * Why the issuer is compared exactly rather than pattern-matched. A loose
 * /^https:\/\/.+\/realms\/master$/ is nearly worthless here, because the scheme
 * and host in the issuer come from KC_HOSTNAME, which the compose always renders
 * from instance_url as a complete https:// URL. So the https:// prefix matches
 * unconditionally and the `.+` swallows any host. Swap KC_HOSTNAME from
 * {{ instance_url }} to {{ instance_host }} and the port is silently dropped:
 * that pattern still matches, while token validation breaks in every federated
 * app. The harness builds KEYCLOAK_URL and instance_url from the same scheme,
 * host and port, so the exact value is available and free.
 *
 * Why step 3 exists. Discovery serves fine on an instance with NO admin user at
 * all, so step 2 alone cannot detect a broken bootstrap-credential injection.
 * Keycloak has already renamed those variables once (KEYCLOAK_ADMIN ->
 * KC_BOOTSTRAP_ADMIN_USERNAME in 26.0); the next rename would leave the operator
 * with an IdP they cannot sign in to while smoke stayed green. Verified
 * load-bearing: the same request with a wrong password returns 400
 * invalid_grant, and the assertion demands 200.
 *
 * Why this asserts instead of `test.skip` on a missing URL, unlike the other
 * specs in this repo. CI never runs the suite as a whole: ci_greffer_smoke.py
 * runs one spec at a time and always sets that greffon's URL, so a skip here
 * could only fire in a manual repo-wide `npm test`, where every spec skipping is
 * a vacuous green anyway. Failing loudly says so out loud. Deliberate, please
 * do not "fix" it back to a skip.
 */
const base = (URL || '').replace(/\/$/, '');

test.describe('Keycloak', () => {
  test('fresh install, discovery issuer is exact and the bootstrap admin works', async ({ request }) => {
    expect(URL, 'KEYCLOAK_URL is not set (ci_greffer_smoke.py sets it per greffon)').toBeTruthy();

    // Budget stays under playwright.config.ts's 180s per-test timeout, which
    // would otherwise kill the test before toPass gave up.
    await expect(async () => {
      const resp = await request.get(`${base}/realms/master/.well-known/openid-configuration`);
      expect(resp.status(), 'discovery endpoint not yet 200').toBe(200);

      const doc = await resp.json();
      expect(doc.issuer, 'issuer must be exactly the instance origin').toBe(`${base}/realms/master`);
      expect(doc.authorization_endpoint, 'missing authorization_endpoint').toBe(
        `${base}/realms/master/protocol/openid-connect/auth`,
      );
      expect(doc.jwks_uri, 'missing jwks_uri').toBe(
        `${base}/realms/master/protocol/openid-connect/certs`,
      );
    }).toPass({ timeout: 150_000, intervals: [5_000] });

    // These two literals MUST match smoke_test.json's `required_config`, which is
    // what pins them for the deploy. Drop the pin and ci_greffer_smoke.py invents a
    // random password, this returns 400 invalid_grant, the test fails, and that
    // coupling is the point: it keeps `required_config` load-bearing, not decorative.
    const token = await request.post(`${base}/realms/master/protocol/openid-connect/token`, {
      form: {
        grant_type: 'password',
        client_id: 'admin-cli',
        username: 'admin',
        password: 'KcSmokeAdmin7431QwertyZxcvbn9024',
      },
    });
    expect(token.status(), 'bootstrap admin could not obtain a token').toBe(200);
    expect((await token.json()).access_token, 'no access_token in the grant response').toBeTruthy();
  });
});
