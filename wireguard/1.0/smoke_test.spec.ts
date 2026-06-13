import { test, expect } from '@playwright/test';

// The smoke target is wg-easy's WEB UI (Tier A, nginx-fronted). The WireGuard
// tunnel itself is the Tier-C UDP port and isn't browser-testable; a rendered
// web UI is a sufficient liveness check that the stack came up.
const URL = process.env.WIREGUARD_URL!;

test.describe('WireGuard (wg-easy)', () => {
  test('serves the wg-easy web UI', async ({ page }) => {
    test.skip(!URL, 'WIREGUARD_URL not set');

    const base = URL.replace(/\/$/, '');
    // `/` meta-refreshes to /login (the admin was created at first boot from
    // INIT_*) or to /setup (the first-boot wizard). The CI harness DOES set an
    // INIT_PASSWORD (ci_greffer_smoke.build_configurations generates one), so a
    // missing password is not why CI lands on /setup. Rather, the greffer-only
    // CI allocates no L4 port, so INIT_HOST/INIT_PORT render empty and wg-easy's
    // unattended first-boot init does not complete, leaving the /setup wizard.
    // A real deploy (with an L4 endpoint) lands on /login. networkidle lets the
    // redirect settle on whichever surface applies; the assertions below hold on
    // both. NOTE: because CI cannot complete init, this smoke is a liveness
    // check only and does not verify the admin login. Exercising login would
    // require the harness to provision an L4 endpoint (tracked follow-up).
    await page.goto(base, { waitUntil: 'networkidle', timeout: 60_000 });

    // Assert a SINGLE locator. A `.or()` of password-input + "Sign In" resolves
    // to two elements on the login screen and trips Playwright strict mode, and
    // the password field isn't present on the setup wizard at all. The Nuxt app
    // root and the page title are present on BOTH surfaces and prove the SPA
    // mounted — i.e. the stack served the app, not a 502/error page.
    await expect(page.locator('#__nuxt')).toBeVisible({ timeout: 30_000 });
    await expect(page).toHaveTitle(/wireguard/i);
  });
});
