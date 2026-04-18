import { test, expect } from '@playwright/test';

const URL = process.env.OPENCLAW_URL!;

/**
 * OpenClaw happy path with URL template vars:
 *   - Gateway dashboard renders
 *   - Connect succeeds the origin check (no "origin not allowed")
 *   - Downstream error is at most "pairing required" (an OpenClaw product
 *     step, not a greffon-config bug)
 */
test.describe('OpenClaw', () => {
  test('gateway dashboard Connect passes origin check', async ({ page }) => {
    test.skip(!URL, 'OPENCLAW_URL not set');

    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});
    await expect(page.getByText(/Gateway Dashboard/i).first()).toBeVisible({ timeout: 30_000 });

    const token = page.locator('input[type="password"]').first();
    await token.fill('greffon');
    await page.getByRole('button', { name: /connect/i }).first().click();

    // Wait enough time for the WebSocket to attempt + fail in whichever way.
    await page.waitForTimeout(4_000);

    const origin = page.getByText(/origin not allowed|allowedOrigins/i);
    const pairing = page.getByText(/pairing required|not paired/i);

    const originVisible = await origin.isVisible({ timeout: 2_000 }).catch(() => false);
    const pairingVisible = await pairing.isVisible({ timeout: 2_000 }).catch(() => false);

    expect(originVisible, 'origin should be allowed by the gateway now').toBe(false);
    // pairingVisible may be true (expected) or false (connection progressed further).
    // Either way, not an origin bug.
  });
});
