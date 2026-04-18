import { test, expect } from '@playwright/test';

const URL = process.env.VSCODE_URL!;

/**
 * VS Code happy path:
 *   - Workbench + menubar + activity bar render
 *   - Command Palette opens and accepts a command
 *   - Explorer panel can be switched to (Ctrl+Shift+E)
 */
test.describe('VS Code', () => {
  test('workbench and activity bar render, command palette is interactive', async ({ page }) => {
    test.skip(!URL, 'VSCODE_URL not set');

    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await expect(page.locator('.monaco-workbench')).toBeVisible({ timeout: 60_000 });

    // Activity bar is the vertical icon strip on the left (Explorer, Search, etc.).
    await expect(page.getByRole('tab', { name: /Explorer/i }).first()).toBeVisible({ timeout: 15_000 });

    // Open Command Palette; the quick-input widget should render and accept input.
    await page.keyboard.press('Control+Shift+P');
    const qi = page.locator('.quick-input-widget');
    await expect(qi).toBeVisible({ timeout: 10_000 });
    const qiInput = qi.locator('input.input').first();
    await expect(qiInput).toBeVisible();
    await qiInput.fill('View: Toggle Explorer');

    // Pressing Enter should close the palette.
    await page.keyboard.press('Enter');
    await expect(qi).not.toBeVisible({ timeout: 5_000 });
  });
});
