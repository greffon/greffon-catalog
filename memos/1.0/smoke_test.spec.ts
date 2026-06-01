import { test, expect } from '@playwright/test';

const URL = process.env.MEMOS_URL!;

/**
 * Memos happy path on a fresh install: the server is healthy and the SPA shell
 * loads. Memos (:stable) serves its REST surface under versioned gRPC-gateway
 * paths and renders the sign-up/sign-in form client-side, so we assert the
 * stable /healthz signal (returns "Service ready.") plus the React mount
 * (`#root`) rather than a raw-HTML password field. Verified live against
 * neosmemo/memos:stable.
 */
test.describe('Memos', () => {
  test('serves the app (healthz + SPA shell)', async ({ page, request }) => {
    test.skip(!URL, 'MEMOS_URL not set');

    const base = URL.replace(/\/$/, '');

    // /healthz is the stable readiness signal across Memos versions.
    const health = await request.get(`${base}/healthz`, { timeout: 30_000 });
    expect(health.ok(), `GET /healthz -> ${health.status()}`).toBe(true);

    // SPA mounts into <div id="root">; assert it's present and the app renders
    // content into it (first-run shows the create-host / sign-up form).
    await page.goto(base, { waitUntil: 'networkidle', timeout: 60_000 });
    await expect(page.locator('#root')).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('#root')).not.toBeEmpty({ timeout: 30_000 });
  });
});
