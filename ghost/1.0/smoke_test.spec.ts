import { test, expect } from '@playwright/test';

const URL = process.env.GHOST_URL!;

test.use({ video: 'on' });

/**
 * Ghost minimal use case — fresh install:
 *
 *   1. Wait through cold-start (Ghost + MySQL run DB migrations and Ghost
 *      serves a "We'll be right back" maintenance page until they finish;
 *      cold start is 60–120s).
 *   2. Hit the setup-status admin API. On a fresh DB it returns
 *      `{"setup":[{"status":false}]}` — the canonical "Ghost is up and
 *      no admin has signed up yet" signal.
 *
 * Why this endpoint and not the visual `/ghost/` setup wizard:
 * Ghost issues a canonical-URL 301 from every HTML route to whatever the
 * `url` config says. In the local-greffer probe the assigned host port
 * never matches the configured URL (kernel TIME_WAIT prevents the port
 * allocator from reusing freed ports), so the browser follows the 301 to
 * a dead URL. The setup-status JSON endpoint is exempt from that
 * redirect, so it's the only reliable smoke target. The browser renders
 * the JSON as text, which is enough to make the video useful.
 */
test.describe('Ghost', () => {
  test('fresh install — setup API reports no admin yet', async ({ page }) => {
    test.skip(!URL, 'GHOST_URL not set');

    const setupApi = `${URL.replace(/\/$/, '')}/ghost/api/admin/authentication/setup/`;

    // Poll until cold-start migrations finish. While Ghost is still
    // migrating it returns 503 maintenance HTML on every route, including
    // this API endpoint. Once it returns valid JSON with the setup flag,
    // we know Ghost is fully up.
    await expect(async () => {
      const resp = await page.goto(setupApi, {
        waitUntil: 'domcontentloaded',
        timeout: 15_000,
      });
      expect(resp?.status(), 'setup API not yet 200').toBe(200);
      const body = await page.content();
      expect(body, 'expected fresh-install setup payload').toMatch(
        /"setup"\s*:\s*\[\s*\{\s*"status"\s*:\s*false/,
      );
    }).toPass({ timeout: 180_000, intervals: [3_000] });
  });
});
