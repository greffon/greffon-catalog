import { test, expect } from '@playwright/test';

const URL = process.env.EXCALIDRAW_URL!;

/**
 * Excalidraw happy path: the static client loads and the drawing canvas
 * renders. The app is browser-only (no backend), so a successful load of the
 * canvas surface is the meaningful signal.
 */
test.describe('Excalidraw', () => {
  test('loads the whiteboard canvas', async ({ page }) => {
    test.skip(!URL, 'EXCALIDRAW_URL not set');

    const base = URL.replace(/\/$/, '');
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    // TODO: confirm selector against a live deploy. Excalidraw renders an
    // HTML <canvas> for the drawing surface; its presence is the stable
    // landmark that the SPA mounted.
    const canvas = page.locator('canvas').first();
    await expect(canvas).toBeVisible({ timeout: 30_000 });
  });
});
