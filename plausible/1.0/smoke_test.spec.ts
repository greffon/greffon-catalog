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

    // Plausible CE normally routes to /activate after register, gated on an
    // emailed verification code. Without SMTP the code never arrives. Some
    // CE builds auto-activate when MAILER_ADAPTER is unset — try /sites/new
    // and see if the add-site form renders. If it redirects back to
    // /activate or /login, annotate and still pass (register UI works).
    await page.goto(`${URL.replace(/\/$/, '')}/sites/new`, { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {});

    const domainField = page
      .locator('input[name="site[domain]"], input[name="domain"], input#site_domain')
      .first();
    const canAddSite = await domainField.isVisible({ timeout: 5_000 }).catch(() => false);

    if (!canAddSite) {
      test.info().annotations.push({
        type: 'deployment-blocker',
        description:
          `Plausible redirected post-register to ${page.url()}. Most likely ` +
          'email activation is blocking (no SMTP configured). Register form ' +
          'still accepts input; add-site flow is not reachable without SMTP.',
      });
      return;
    }

    const domain = `e2e-${Date.now()}.example.com`;
    await domainField.fill(domain);
    await page.locator('button[type="submit"], input[type="submit"]').first().click();
    await page.waitForLoadState('domcontentloaded', { timeout: 15_000 }).catch(() => {});

    // Assert we reached the tracking-snippet page. The snippet itself may be
    // rendered in a <pre>/<code>/<textarea> and is not in innerText on all
    // CE builds; the page's heading text "Paste this snippet" is the stable
    // landmark.
    const body = await page.locator('body').innerText({ timeout: 15_000 }).catch(() => '');
    const sawSnippetPage =
      /paste this snippet|plausible\.js|data-domain/i.test(body) ||
      /\/snippet(\?|$)/.test(page.url());
    expect(sawSnippetPage, `snippet page on ${page.url()}; body preview=${body.slice(0, 200)}`).toBe(true);
  });
});
