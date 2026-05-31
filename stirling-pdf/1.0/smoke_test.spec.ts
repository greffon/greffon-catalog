import { test, expect } from '@playwright/test';

const URL = process.env.STIRLING_PDF_URL!;

/**
 * Stirling-PDF happy path: the home page serves and the tools dashboard
 * actually renders. With SECURITY_ENABLELOGIN=false there is no login wall,
 * so the landing page shows the PDF tools grid directly. We assert a visible
 * tool entry (not just the document <title>) so a broken frontend fails the
 * gate instead of passing on a bare HTML shell.
 */
test.describe('Stirling-PDF', () => {
  test('renders the PDF tools dashboard', async ({ page }) => {
    test.skip(!URL, 'STIRLING_PDF_URL not set');

    const base = URL.replace(/\/$/, '');
    await page.goto(base, { waitUntil: 'networkidle', timeout: 90_000 });

    // A real tool card must be visible — proves the SPA mounted and the tools
    // grid rendered, not just that an HTML doc with a Stirling <title> loaded.
    // TODO: confirm the exact copy against a live deploy; "Merge" is one of
    // the always-present core tools. Fall back to any link pointing at a tool
    // route (/merge-pdfs, /compress-pdf, etc.).
    const toolLandmark = page
      .getByRole('link', { name: /merge/i })
      .or(page.locator('a[href*="merge"], a[href*="compress"], a[href*="convert"]'))
      .first();
    await expect(toolLandmark).toBeVisible({ timeout: 45_000 });
  });
});
