import { test, expect } from '@playwright/test';

const URL = process.env.MULTICA_URL!;

/**
 * Multica happy path: the web frontend serves and renders its app. This
 * greffon hosts the server stack (web + Go API + Postgres); the agent daemon
 * is a separate local process and is NOT exercised here. Verified live against
 * ghcr.io/multica-ai/multica-web:v0.3.13 — the frontend renders a <main> with
 * the Multica wordmark and a "Get started" CTA.
 */
test.describe('Multica', () => {
  test('serves the web frontend', async ({ page }) => {
    test.skip(!URL, 'MULTICA_URL not set');

    const base = URL.replace(/\/$/, '');
    await page.goto(base, { waitUntil: 'networkidle', timeout: 60_000 });

    // Real landmarks (verified live): the <main> region renders, the Multica
    // wordmark is present, and a "Get started" action is offered.
    await expect(page.locator('main').first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/multica/i).first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/get started/i).first()).toBeVisible({ timeout: 30_000 });
  });
});
