import { test, expect } from '@playwright/test';

const URL = process.env.GLITCHTIP_URL!;

/**
 * GlitchTip full happy path: admin seed container ran on first startup, so
 * admin@greffon.io / Admin123! already exists. Log in via the SPA's
 * /_allauth/browser/v1/auth/login API (POSTed by the form submit) and verify
 * the auth state changes to authenticated.
 */
test.describe('GlitchTip', () => {
  test('seeded admin can log in', async ({ page }) => {
    test.skip(!URL, 'GLITCHTIP_URL not set');

    // SPA fetches /_allauth/browser/v1/auth/session on load — that response
    // sets the csrftoken cookie. Wait for the network to go idle so the
    // cookie is in place before we POST the login.
    await page.goto(URL, { waitUntil: 'networkidle', timeout: 60_000 });

    const email = page.locator('input[type="email"]').first();
    await expect(email).toBeVisible({ timeout: 30_000 });
    await email.fill('admin@greffon.io');
    await page.locator('input[type="password"]').first().fill('Admin123!');

    // Wait for the auth/login POST to come back authenticated.
    const loginResp = page.waitForResponse(
      r => r.url().includes('/_allauth/browser/v1/auth/login') && r.request().method() === 'POST',
      { timeout: 15_000 }
    );
    await page.getByRole('button', { name: /log\s*in/i }).first().click();
    const resp = await loginResp;

    expect(resp.status(), 'login response status').toBeGreaterThanOrEqual(200);
    expect(resp.status(), 'login response status').toBeLessThan(300);

    const body = await resp.json();
    expect(body?.meta?.is_authenticated, 'is_authenticated in response').toBe(true);
    expect(body?.data?.user?.email, 'logged-in user email').toBe('admin@greffon.io');
  });
});
