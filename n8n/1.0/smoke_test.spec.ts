import { test, expect } from '@playwright/test';

const URL = process.env.N8N_URL!;

/**
 * n8n happy path on a fresh install: the app serves and the owner-setup
 * screen renders (n8n's first run asks you to create the owner account). We
 * hit the REST healthz endpoint first (JSON, exempt from SPA routing), then
 * confirm the SPA shell.
 */
test.describe('n8n', () => {
  test('serves the app and healthz', async ({ page, request }) => {
    test.skip(!URL, 'N8N_URL not set');

    const base = URL.replace(/\/$/, '');

    // n8n exposes /healthz returning {status:"ok"} when up.
    const health = await request.get(`${base}/healthz`, { timeout: 30_000 });
    expect(health.ok(), `GET /healthz -> ${health.status()}`).toBe(true);

    // The SPA loads — first run shows the create-owner setup form.
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    // TODO: confirm selector against a live deploy. The setup/login form has a
    // password input — the stable landmark on a fresh instance.
    const pw = page.locator('input[type="password"]').first();
    await expect(pw).toBeVisible({ timeout: 45_000 });
  });
});
