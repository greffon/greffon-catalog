import { test, expect } from '@playwright/test';

const URL = process.env.MULTICA_URL!;

/**
 * Multica happy path: the web frontend serves AND can reach its backend API.
 * This greffon hosts the server stack (web + Go API + Postgres); the agent
 * daemon is a separate local process and is NOT exercised here.
 *
 * The frontend proxies /api/* to the backend at the hardcoded host
 * `backend:8080` — the compose adds a `backend` network alias so this
 * resolves. We assert the proxied API actually works (not just that the
 * landing page renders), since a broken alias returns 500s while the page
 * still loads. Verified live against v0.3.13: /api/config -> 200, and
 * auth-gated endpoints -> 401 (reached the backend, just unauthenticated).
 */
test.describe('Multica', () => {
  test('serves the frontend and proxies the backend API', async ({ page, request }) => {
    test.skip(!URL, 'MULTICA_URL not set');

    const base = URL.replace(/\/$/, '');

    // The /api proxy must reach the backend. /api/config is public and returns
    // 200 when the frontend->backend alias resolves; a broken alias 500s.
    const cfg = await request.get(`${base}/api/config`, { timeout: 30_000 });
    expect(cfg.ok(), `GET /api/config -> ${cfg.status()} (frontend must proxy to backend)`).toBe(true);

    // An auth-gated endpoint should answer 401 (reached backend), not 5xx.
    const me = await request.get(`${base}/api/me`, { timeout: 30_000 });
    expect(me.status(), `GET /api/me -> ${me.status()}`).toBeLessThan(500);

    // The frontend app shell renders.
    await page.goto(base, { waitUntil: 'networkidle', timeout: 60_000 });
    await expect(page.locator('main').first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/multica/i).first()).toBeVisible({ timeout: 30_000 });
  });
});
