import { test } from '@playwright/test';
import { expectNoSeriousA11yViolations } from './helpers';

// Audit I01: public and signed-in pages have no serious/critical WCAG 2.1 A/AA problems.
test.describe('signed out', () => {
  test.use({ storageState: { cookies: [], origins: [] } });
  for (const path of ['/', '/login', '/register', '/recover', '/privacy', '/terms']) {
    test(`a11y ${path}`, async ({ page }) => {
      await page.goto(path);
      await expectNoSeriousA11yViolations(page);
    });
  }
});

for (const path of ['/dashboard', '/certificates', '/settings/security']) {
  test(`a11y ${path} (signed in)`, async ({ page }) => {
    await page.goto(path);
    await page.waitForLoadState('networkidle');
    await expectNoSeriousA11yViolations(page);
  });
}
