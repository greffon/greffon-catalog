import { test, expect } from '@playwright/test';

// The smoke target is wg-easy's WEB UI (Tier A, nginx-fronted). The WireGuard
// tunnel itself is the Tier-C UDP port and isn't browser-testable; the web UI
// rendering is a sufficient liveness check that the stack came up.
const URL = process.env.WIREGUARD_URL!;

test.describe('WireGuard (wg-easy)', () => {
  test('serves the wg-easy web UI login', async ({ page }) => {
    test.skip(!URL, 'WIREGUARD_URL not set');

    const base = URL.replace(/\/$/, '');
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    // wg-easy v15 gates the UI behind the admin login created at first boot
    // via INIT_USERNAME/INIT_PASSWORD (deploy-verified: anonymous API calls
    // 401). Accept either the login surface (a password input / "Sign In")
    // or, if a build renders the app shell first, a visible body — enough to
    // prove the web UI is serving.
    const passwordField = page.locator('input[type="password"]').first();
    const signIn = page.getByText(/sign in|log ?in|password/i).first();
    await expect(passwordField.or(signIn)).toBeVisible({ timeout: 30_000 });
  });
});
