import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';

export const env = (name: string): string => {
  const v = process.env[name];
  if (!v) throw new Error(`${name} is not set (see playwright.config.ts)`);
  return v;
};

/** WCAG 2.1 A/AA problems of serious or critical impact must be zero. */
export async function expectNoSeriousA11yViolations(page: Page) {
  // @axe-core/playwright resolves its own playwright-core patch release; the Page API it
  // uses is the same, only the nominal types differ.
  const axePage = page as unknown as ConstructorParameters<typeof AxeBuilder>[0]['page'];
  const result = await new AxeBuilder({ page: axePage }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const serious = result.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  const summary = serious.map((v) => `${v.id}: ${v.help} (${v.nodes.length} nodes: ${v.nodes[0]?.target.join(' ')})`);
  expect(summary, summary.join('\n')).toEqual([]);
}

export async function choose(page: Page, optionText: string) {
  await page.getByRole('main').locator('label', { hasText: new RegExp(`^${optionText}$`) }).click();
}
