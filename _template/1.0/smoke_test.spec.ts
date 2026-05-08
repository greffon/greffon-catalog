import { test, expect } from '@playwright/test';

// TEMPLATE — copy to <your-greffon>/<version>/smoke_test.spec.ts and rewrite
// for the real app. The smoke test must assert a *user-visible* task works:
// "the dashboard renders", "I can log in", "uploaded file persists".
//
// The instance URL is passed as an env var. Pick a name unique to your
// greffon (the platform sets it from the deploy result). Convention:
// uppercase greffon name + _URL. See vscode/1.0/smoke_test.spec.ts.
//
// **Important — don't navigate to `/` (or any HTML route) for apps with
// canonical-URL redirects.** Apps like Ghost, Nextcloud, etc. redirect
// every HTML route to whatever the operator-configured public URL says.
// In the local-greffer probe environment the configured URL won't match
// the assigned host port (kernel TIME_WAIT prevents the port allocator
// from reusing freed ports), so following that redirect lands on a dead
// URL. Don't rely on admin SPA shells (`/ghost/`, `/admin`) either —
// those get redirected too in Ghost 5.x and similar.
//
// Use a JSON API endpoint that returns a stable shape on a fresh install.
// Examples: Ghost → `/ghost/api/admin/authentication/setup/` returns
// `{"setup":[{"status":false}]}`; a 401 from a content API is also a
// fine "is the app up?" signal. See ghost/1.0/smoke_test.spec.ts for the
// canonical example.

const URL = process.env.TEMPLATE_URL!;

test.describe('TEMPLATE', () => {
  test('app renders the landing page', async ({ page }) => {
    test.skip(!URL, 'TEMPLATE_URL not set');

    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });

    // TODO: replace with a real assertion — a heading, a logo, a known
    // selector that proves the app actually rendered.
    await expect(page.locator('body')).toBeVisible({ timeout: 30_000 });
  });
});
