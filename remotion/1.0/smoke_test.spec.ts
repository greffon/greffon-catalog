import { test, expect } from '@playwright/test';

const URL = process.env.REMOTION_URL!;

/**
 * Remotion Studio happy path:
 *   - The Studio shell loads (document title + the canvas/timeline app root)
 *   - The baked "HelloWorld" composition shows up in the sidebar, proving the
 *     bundler picked up the seeded starter project from the named volume.
 */
test.describe('Remotion Studio', () => {
  test('studio shell loads and lists the starter composition', async ({ page }) => {
    test.skip(!URL, 'REMOTION_URL not set');

    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await expect(page).toHaveTitle(/Remotion Studio/i, { timeout: 30_000 });

    await expect(page.getByText('HelloWorld').first()).toBeVisible({ timeout: 60_000 });
  });
});
