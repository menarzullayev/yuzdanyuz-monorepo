import { expect, test } from '@playwright/test';
import { choose, env, expectNoSeriousA11yViolations } from './helpers';

test.describe('student exam flow (audit H10)', () => {
  test('answer, reload restores answers, finish shows the score', async ({ page }) => {
    await page.goto('/exams');
    await expectNoSeriousA11yViolations(page);

    const mock = env('E2E_MOCK_LONG');
    await page.goto(`/exams/${mock}`);
    await expect(page.getByRole('timer')).toBeVisible();
    await expectNoSeriousA11yViolations(page);

    const next = page.getByRole('button', { name: 'Keyingi' });
    await choose(page, '5');
    await expect(page.getByText('Saqlandi')).toBeVisible();
    await next.click();
    await choose(page, 'Toshkent');
    await expect(page.getByText('Saqlandi')).toBeVisible();
    await next.click();
    await choose(page, '2');
    await choose(page, '7');
    await expect(page.getByText('Saqlandi')).toBeVisible();

    await page.reload();
    await page.getByRole('navigation', { name: 'Savollar' }).getByRole('button', { name: '3' }).click();
    await expect(page.getByRole('checkbox', { checked: true })).toHaveCount(2);
    // A reload is not a violation (Next.js keeps its own role=alert route announcer).
    await expect(page.getByText(/^Ogohlantirish:/)).toHaveCount(0);

    await page.getByRole('button', { name: 'Yakunlash' }).click();
    await expect(page.getByText('Javob berilmagan savollar: 1.')).toBeVisible();
    await page.getByRole('button', { name: 'Ha, yakunlash' }).click();

    await page.waitForURL(/\/exams\/result\//);
    await expect(page.getByText('75%')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('Toʻgʻri javoblar: 3')).toBeVisible();
    await expectNoSeriousA11yViolations(page);
  });

  test('the exam is handed in when the time runs out', async ({ page }) => {
    test.setTimeout(150_000);
    await page.goto(`/exams/${env('E2E_MOCK_SHORT')}`);
    await choose(page, '5');
    await expect(page.getByText('Saqlandi')).toBeVisible();
    await page.waitForURL(/\/exams\/result\//, { timeout: 90_000 });
    await expect(page.getByText('25%')).toBeVisible({ timeout: 30_000 });
  });
});
