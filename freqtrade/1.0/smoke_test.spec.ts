import { test, expect } from '@playwright/test';

const URL = process.env.FREQTRADE_URL!;

/**
 * Freqtrade happy path:
 *   - Deploys with default SampleStrategy.py (baked into metadata default_value)
 *   - REST/webserver mode comes up on :8080
 *   - The root endpoint (or /api/v1/ping) responds
 */
test.describe('Freqtrade', () => {
  test('webserver responds on the greffon URL', async ({ page }) => {
    test.skip(!URL, 'FREQTRADE_URL not set');

    const resp = await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    expect(resp, 'response object').not.toBeNull();
    expect(resp!.status(), 'HTTP status').toBeGreaterThanOrEqual(200);
    expect(resp!.status(), 'HTTP status').toBeLessThan(500);
  });
});
