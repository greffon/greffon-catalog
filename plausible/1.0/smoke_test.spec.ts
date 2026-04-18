import { test, expect } from '@playwright/test';

const URL = process.env.PLAUSIBLE_URL!;

/**
 * Plausible happy path: first user registers via /register, and the
 * post-register confirmation page renders (Plausible sends a verification
 * email we can't deliver in dev; we check the "email sent" / "activate" UI
 * instead of the dashboard).
 */
test.describe('Plausible Analytics', () => {
  test('register renders and accepts first-user signup', async ({ page }) => {
    test.skip(!URL, 'PLAUSIBLE_URL not set');

    await page.goto(`${URL.replace(/\/$/, '')}/register`, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    const email = page.locator('input[type="email"], input[name*="email" i]').first();
    await expect(email).toBeVisible({ timeout: 30_000 });
    await email.fill(`admin-${Date.now()}@example.com`);

    const nameField = page.locator('input[name*="name" i]').first();
    if (await nameField.isVisible({ timeout: 2_000 }).catch(() => false)) {
      await nameField.fill('Admin');
    }

    const passwords = page.locator('input[type="password"]');
    const pwCount = await passwords.count();
    await passwords.nth(0).fill('Password123!');
    if (pwCount > 1) await passwords.nth(1).fill('Password123!');

    // "Create my account →" — use the actual button copy, not the sidebar step label.
    await Promise.all([
      page.waitForURL(u => !/\/register$/.test(u.toString()), { timeout: 20_000 }).catch(() => {}),
      page.getByRole('button', { name: /create.*account/i }).first().click(),
    ]);
    await page.waitForLoadState('domcontentloaded', { timeout: 15_000 }).catch(() => {});

    // Post-register expected states: activation-sent page, onboarding ("add a
    // site"), or dashboard. All acceptable; an inline error about disabled
    // registration would be the failure case.
    const regDisabled = page.getByText(/registration.*disabled|not available/i).first();
    const failed = await regDisabled.isVisible({ timeout: 2_000 }).catch(() => false);
    expect(failed, 'registration should not be disabled').toBe(false);

    // Prove we left the bare /register page — a successful submit navigates
    // to activation/onboarding/home.
    expect(page.url(), 'post-register URL').not.toMatch(/\/register$/);
  });
});
