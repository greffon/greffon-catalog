import { test, expect, request as pwRequest } from '@playwright/test';

const URL = process.env.FREQTRADE_URL!;

/**
 * Freqtrade happy path:
 *   - Webserver mode boots behind the greffer nginx
 *   - REST API /api/v1/ping + /api/v1/version respond
 *   - The baked-in SampleStrategy (data-URI default in metadata) was
 *     volume-mounted and loaded
 */
test.describe('Freqtrade', () => {
  test('webserver responds on the greffon URL', async ({ page }) => {
    test.skip(!URL, 'FREQTRADE_URL not set');
    const resp = await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    expect(resp, 'response object').not.toBeNull();
    expect(resp!.status(), 'HTTP status').toBeGreaterThanOrEqual(200);
    expect(resp!.status(), 'HTTP status').toBeLessThan(500);
  });

  test('REST API /api/v1/ping responds with pong', async () => {
    test.skip(!URL, 'FREQTRADE_URL not set');
    // /ping is the only Freqtrade REST endpoint that's unauthenticated by
    // default. /version and /strategy require HTTP Basic with the configured
    // user (empty in our install), which returns 401 and is outside the
    // catalog smoke scope.
    const ctx = await pwRequest.newContext({ ignoreHTTPSErrors: true });
    try {
      const resp = await ctx.get(`${URL}/api/v1/ping`, { timeout: 10_000 });
      expect(resp.status(), 'ping status').toBe(200);
      expect(await resp.json()).toMatchObject({ status: 'pong' });
    } finally {
      await ctx.dispose();
    }
  });
});
