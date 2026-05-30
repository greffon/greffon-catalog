import { test, expect } from '@playwright/test';

const URL = process.env.STIRLING_PDF_URL!;

/**
 * Stirling-PDF happy path: the home page serves and the tool dashboard
 * renders. With SECURITY_ENABLELOGIN=false there is no login wall, so the
 * landing page shows the PDF tools grid directly.
 */
test.describe('Stirling-PDF', () => {
  test('serves the tools dashboard', async ({ page }) => {
    test.skip(!URL, 'STIRLING_PDF_URL not set');

    const base = URL.replace(/\/$/, '');
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 90_000 });

    // TODO: confirm selector against a live deploy. Stirling-PDF renders its
    // brand name in the navbar/title; assert the page title or a visible
    // "Stirling" wordmark as the landmark.
    await expect(page).toHaveTitle(/stirling/i, { timeout: 30_000 });
  });
});
