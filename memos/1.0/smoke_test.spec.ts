import { test, expect } from '@playwright/test';

const URL = process.env.MEMOS_URL!;

/**
 * Memos happy path on a fresh install: the server is healthy AND the usable
 * first-run sign-up surface renders. Memos (:stable) serves its REST API under
 * versioned gRPC-gateway paths (so /api/v1/workspace/* 404s) and renders the
 * form client-side, so we assert /healthz ("Service ready.") plus the real
 * signup landmarks rather than just a non-empty #root (which an error boundary
 * would also satisfy). Verified live against neosmemo/memos:stable: a fresh
 * instance redirects to /auth/signup with a "Create your account" form, a
 * username + password field, and a "Sign up" button ("registering as the Site
 * Host").
 */
test.describe('Memos', () => {
  test('serves the first-run sign-up surface', async ({ page, request }) => {
    test.skip(!URL, 'MEMOS_URL not set');

    const base = URL.replace(/\/$/, '');

    // /healthz is the stable readiness signal across Memos versions.
    const health = await request.get(`${base}/healthz`, { timeout: 30_000 });
    expect(health.ok(), `GET /healthz -> ${health.status()}`).toBe(true);

    // The client-rendered sign-up form must actually be usable — assert the
    // password field + the Sign up control, not just that #root has content.
    await page.goto(base, { waitUntil: 'networkidle', timeout: 60_000 });
    await expect(page.locator('input[type="password"]').first())
      .toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole('button', { name: /sign up/i }).first())
      .toBeVisible({ timeout: 30_000 });
  });
});
