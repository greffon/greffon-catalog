import { test, expect } from '@playwright/test';

const URL = process.env.VAULTWARDEN_URL!;

/**
 * Vaultwarden happy path on a fresh install: the web vault loads and the
 * registration/login UI renders. We assert against the JSON config endpoint
 * first (exempt from any canonical-URL redirect), then confirm the SPA shell.
 */
test.describe('Vaultwarden', () => {
  test('serves the web vault and api config', async ({ page, request }) => {
    test.skip(!URL, 'VAULTWARDEN_URL not set');

    const base = URL.replace(/\/$/, '');

    // /api/config returns JSON on a healthy instance regardless of DOMAIN
    // mismatch — the most reliable "is it actually up" signal.
    const cfg = await request.get(`${base}/api/config`, { timeout: 30_000 });
    expect(cfg.ok(), `GET /api/config -> ${cfg.status()}`).toBe(true);

    // The web vault SPA shell should load.
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    // TODO: confirm selector against a live deploy. The Bitwarden web vault
    // renders a login/create-account surface; an email input is the most
    // stable landmark on a fresh, signups-allowed instance.
    const email = page.locator('input[type="email"], input[name*="email" i]').first();
    await expect(email).toBeVisible({ timeout: 30_000 });
  });
});
