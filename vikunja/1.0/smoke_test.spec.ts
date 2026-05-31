import { test, expect } from '@playwright/test';

const URL = process.env.VIKUNJA_URL!;

/**
 * Vikunja primary-task smoke: a new user can register AND authenticate.
 *
 * We drive the API rather than the SPA so the assertions are unambiguous and
 * exempt from frontend routing quirks:
 *   1. GET  /api/v1/info      — backend + DB up; assert registration is
 *      actually ENABLED (a positive signal — a deploy with registration
 *      disabled fails here instead of silently passing).
 *   2. POST /api/v1/register  — create the first user (exercises the writable
 *      SQLite volume).
 *   3. POST /api/v1/login     — authenticate; a returned JWT proves the
 *      service secret signs tokens and the full signup→login task works.
 * Then we confirm the SPA shell loads for a human-facing sanity check.
 */
test.describe('Vikunja', () => {
  test('registers and authenticates a new user', async ({ page, request }) => {
    test.skip(!URL, 'VIKUNJA_URL not set');

    const base = URL.replace(/\/$/, '');

    // 1. Backend up + registration enabled (positive signal).
    const infoResp = await request.get(`${base}/api/v1/info`, { timeout: 30_000 });
    expect(infoResp.ok(), `GET /api/v1/info -> ${infoResp.status()}`).toBe(true);
    const info = await infoResp.json();
    expect(info.registration_enabled, 'registration must be enabled on a fresh instance').toBe(true);

    // 2. Register the first user — exercises the writable /db volume.
    const ts = Date.now();
    const creds = { username: `e2e${ts}`, email: `e2e${ts}@example.com`, password: 'Sup3r-Vik-Pass' };
    const reg = await request.post(`${base}/api/v1/register`, { data: creds, timeout: 30_000 });
    expect(reg.ok(), `POST /api/v1/register -> ${reg.status()} ${await reg.text()}`).toBe(true);

    // 3. Authenticate — a JWT proves the service secret + auth path work.
    const login = await request.post(`${base}/api/v1/login`, {
      data: { username: creds.username, password: creds.password },
      timeout: 30_000,
    });
    expect(login.ok(), `POST /api/v1/login -> ${login.status()}`).toBe(true);
    const token = (await login.json()).token;
    expect(typeof token, 'login should return a JWT token').toBe('string');

    // 4. Human-facing sanity: the SPA shell loads.
    await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await expect(page.locator('body')).toContainText(/.+/, { timeout: 30_000 });
  });
});
