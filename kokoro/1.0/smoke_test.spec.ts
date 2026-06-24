import { test, expect } from '@playwright/test';

const URL = process.env.KOKORO_URL!;

/**
 * Kokoro TTS happy path (through the redirect/proxy front):
 *   - the root "/" redirects to the /web UI (so the platform's Open button lands
 *     somewhere useful instead of Kokoro's bare-root 404)
 *   - /health reports healthy once the model has loaded
 *   - the web UI renders its FastKoko shell
 *   - the OpenAI-compatible endpoint actually synthesizes audio
 *
 * The model loads for up to ~90s on a cold CPU boot, during which the proxy
 * returns 502; poll /health first so the asserts below aren't racing the load.
 */
test.describe('Kokoro TTS', () => {
  test('redirect, health, web UI, and speech synthesis work', async ({ page, request }) => {
    test.skip(!URL, 'KOKORO_URL not set');

    await expect(async () => {
      const r = await request.get(`${URL}/health`, { timeout: 10_000 });
      expect(r.ok()).toBeTruthy();
      expect(await r.json()).toMatchObject({ status: 'healthy' });
    }).toPass({ timeout: 180_000, intervals: [5_000] });

    // Root redirects to the web UI.
    const root = await request.get(`${URL}/`, { maxRedirects: 0 });
    expect(root.status()).toBe(302);
    expect(root.headers()['location']).toContain('/web');

    await page.goto(`${URL}/web/`, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await expect(page.getByRole('heading', { name: /FastKoko/i })).toBeVisible({ timeout: 30_000 });

    // The actual function: synthesize a short clip and assert we got audio back.
    const speech = await request.post(`${URL}/v1/audio/speech`, {
      data: {
        model: 'kokoro',
        input: 'Greffon and Remotion, working together.',
        voice: 'af_bella',
        response_format: 'mp3',
      },
      timeout: 60_000,
    });
    expect(speech.ok()).toBeTruthy();
    expect(speech.headers()['content-type']).toContain('audio');
    expect((await speech.body()).length).toBeGreaterThan(1000);
  });
});
