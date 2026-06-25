import { test, expect } from '@playwright/test';

const URL = process.env.GLITCHTIP_URL!;

/**
 * GlitchTip full happy path: admin seed container ran on first startup, so
 * admin@greffon.io / Admin123! already exists. Log in via the SPA's
 * /_allauth/browser/v1/auth/login API (POSTed by the form submit) and verify
 * the auth state changes to authenticated.
 */
test.describe('GlitchTip', () => {
  // GlitchTip's admin (createsuperuser) runs as a one-shot that depends on the
  // migrate job, both of which finish a little AFTER the web container starts
  // serving. The greffer reports "running" as soon as the containers are up, so
  // a smoke that logs in immediately races the seed and the migrate (login then
  // 4xx/5xx because the admin row / tables aren't there yet). Gate every test on
  // the seed being live: poll the allauth login API (in the isolated `request`
  // context, so the UI flows on `page` keep a clean session) until the seeded
  // admin actually authenticates.
  test.beforeEach(async ({ request }) => {
    test.skip(!URL, 'GLITCHTIP_URL not set');
    const base = URL.replace(/\/$/, '');
    await expect
      .poll(
        async () => {
          // GET session first so allauth sets the csrftoken cookie in this
          // request context; then POST login with the matching X-CSRFToken.
          await request.get(`${base}/_allauth/browser/v1/auth/session`);
          const csrf = (await request.storageState()).cookies.find(
            (c) => c.name === 'csrftoken',
          )?.value;
          if (!csrf) return 0;
          const r = await request.post(`${base}/_allauth/browser/v1/auth/login`, {
            headers: { 'X-CSRFToken': csrf, Referer: URL },
            data: { email: 'admin@greffon.io', password: 'Admin123!' },
          });
          return r.status();
        },
        {
          message: 'glitchtip seeded admin never became able to log in',
          timeout: 120_000,
          intervals: [3_000],
        },
      )
      .toBeLessThan(300);
  });

  test('seeded admin can log in', async ({ page }) => {
    test.skip(!URL, 'GLITCHTIP_URL not set');

    // SPA fetches /_allauth/browser/v1/auth/session on load — that response
    // sets the csrftoken cookie. Wait for the network to go idle so the
    // cookie is in place before we POST the login.
    await page.goto(URL, { waitUntil: 'networkidle', timeout: 60_000 });

    const email = page.locator('input[type="email"]').first();
    await expect(email).toBeVisible({ timeout: 30_000 });
    await email.fill('admin@greffon.io');
    await page.locator('input[type="password"]').first().fill('Admin123!');

    // Wait for the auth/login POST to come back authenticated.
    const loginResp = page.waitForResponse(
      r => r.url().includes('/_allauth/browser/v1/auth/login') && r.request().method() === 'POST',
      { timeout: 15_000 }
    );
    await page.getByRole('button', { name: /log\s*in/i }).first().click();
    const resp = await loginResp;

    expect(resp.status(), 'login response status').toBeGreaterThanOrEqual(200);
    expect(resp.status(), 'login response status').toBeLessThan(300);

    const body = await resp.json();
    expect(body?.meta?.is_authenticated, 'is_authenticated in response').toBe(true);
    expect(body?.data?.user?.email, 'logged-in user email').toBe('admin@greffon.io');
  });

  test('admin can create an organization + project and receive a DSN', async ({ page }) => {
    test.skip(!URL, 'GLITCHTIP_URL not set');

    // Log in so subsequent requests share the session + csrftoken cookie.
    await page.goto(URL, { waitUntil: 'networkidle', timeout: 60_000 });
    await page.locator('input[type="email"]').first().fill('admin@greffon.io');
    await page.locator('input[type="password"]').first().fill('Admin123!');
    const loginWait = page.waitForResponse(
      r => r.url().includes('/_allauth/browser/v1/auth/login') && r.request().method() === 'POST',
      { timeout: 15_000 }
    );
    await page.getByRole('button', { name: /log\s*in/i }).first().click();
    await loginWait;

    // Pull the csrftoken Django-style for the /api/0/ DRF endpoints.
    const cookies = await page.context().cookies();
    const csrf = cookies.find(c => c.name === 'csrftoken')?.value;
    expect(csrf, 'csrftoken cookie').toBeTruthy();
    const headers = {
      'X-CSRFToken': csrf!,
      'Content-Type': 'application/json',
      Referer: URL,
    };

    // Create an organization. GlitchTip assigns a slug server-side.
    const orgName = `qa-${Date.now()}`;
    const orgResp = await page.request.post(`${URL}/api/0/organizations/`, {
      headers,
      data: { name: orgName },
    });
    expect(orgResp.status(), `create org status`).toBeGreaterThanOrEqual(200);
    expect(orgResp.status(), `create org status`).toBeLessThan(300);
    const org = await orgResp.json();
    const orgSlug: string = org.slug ?? org.id ?? orgName;

    // Create a team in that org — GlitchTip doesn't auto-create one.
    const teamCreate = await page.request.post(
      `${URL}/api/0/organizations/${orgSlug}/teams/`,
      {
        headers,
        data: { slug: `team-${Date.now()}`, name: 'QA Team' },
      },
    );
    expect(teamCreate.status(), 'create team status').toBeGreaterThanOrEqual(200);
    expect(teamCreate.status(), 'create team status').toBeLessThan(300);
    const team = await teamCreate.json();
    const teamSlug: string = team.slug;

    // Create a project in that team.
    const projectName = `my-app-${Date.now()}`;
    const projResp = await page.request.post(
      `${URL}/api/0/teams/${orgSlug}/${teamSlug}/projects/`,
      {
        headers,
        data: { name: projectName, platform: 'javascript' },
      },
    );
    expect(projResp.status(), `create project status`).toBeGreaterThanOrEqual(200);
    expect(projResp.status(), `create project status`).toBeLessThan(300);
    const project = await projResp.json();
    const projectSlug: string = project.slug;

    // The project has at least one DSN key. Shape: https://<pub>@<host>/<N>
    const keysResp = await page.request.get(
      `${URL}/api/0/projects/${orgSlug}/${projectSlug}/keys/`,
      { headers },
    );
    expect(keysResp.status()).toBe(200);
    const keys = await keysResp.json();
    expect(Array.isArray(keys)).toBe(true);
    expect(keys.length, 'project has at least one DSN key').toBeGreaterThan(0);
    const dsn: string = keys[0]?.dsn?.public ?? keys[0]?.dsnPublic ?? '';
    expect(dsn, 'DSN shape').toMatch(/^https?:\/\/[A-Za-z0-9]+@[^/\s]+\/\d+$/);
  });
});
