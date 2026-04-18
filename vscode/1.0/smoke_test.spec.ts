import { test, expect } from '@playwright/test';

const URL = process.env.VSCODE_URL!;

/**
 * VS Code happy path:
 *   - Workbench + activity bar render
 *   - Command Palette opens + accepts input
 *   - A known command ("View: Toggle Explorer") executes and closes the palette
 *
 * The deployed greffon is "VS Code for the Web" (vscode.dev-style) — no backend
 * filesystem or terminal. Deeper user tasks (write file, run code) are out of
 * scope until we swap the catalog entry to code-server.
 */
test.describe('VS Code', () => {
  test('workbench + activity bar render, command palette is interactive', async ({ page }) => {
    test.skip(!URL, 'VSCODE_URL not set');

    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await expect(page.locator('.monaco-workbench')).toBeVisible({ timeout: 60_000 });

    await expect(page.getByRole('tab', { name: /Explorer/i }).first()).toBeVisible({ timeout: 15_000 });

    await page.keyboard.press('Control+Shift+P');
    const qi = page.locator('.quick-input-widget');
    await expect(qi).toBeVisible({ timeout: 10_000 });
    const qiInput = qi.locator('input.input').first();
    await expect(qiInput).toBeVisible();
    await qiInput.fill('View: Toggle Explorer');

    await page.keyboard.press('Enter');
    await expect(qi).not.toBeVisible({ timeout: 5_000 });
  });
});
