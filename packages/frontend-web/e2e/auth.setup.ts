import { expect, test as setup } from '@playwright/test';
import { STUDENT_STATE } from '../playwright.config';
import { env, expectNoSeriousA11yViolations } from './helpers';

setup('student signs in through the login form', async ({ page }) => {
  await page.goto('/login?next=%2Fexams');
  await expectNoSeriousA11yViolations(page);
  await page.getByLabel('Login', { exact: true }).fill(env('E2E_USERNAME'));
  await page.getByLabel('Parol', { exact: true }).fill(env('E2E_PASSWORD'));
  await page.getByRole('button', { name: 'Kirish', exact: true }).click();
  await page.waitForURL((url) => url.pathname === '/exams');
  await expect(page.getByRole('heading', { name: 'Imtihonlar' })).toBeVisible();
  await page.context().storageState({ path: STUDENT_STATE });
});
