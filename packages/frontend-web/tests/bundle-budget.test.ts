import { describe, expect, it } from 'vitest';
import { check, routeEntries } from '../scripts/check-bundle-budget.mjs';

const manifest = `globalThis.__RSC_MANIFEST = globalThis.__RSC_MANIFEST || {};
globalThis.__RSC_MANIFEST["/[locale]/exams/page"] = {"entryJSFiles":{"a":["static/chunks/x.js"],"b":["static/chunks/x.js","static/chunks/y.js"]}};`;

describe('bundle budget (audit I03)', () => {
  it('collects each route\'s entry chunks once', () => {
    expect(routeEntries(manifest)).toEqual({ '/[locale]/exams/page': ['static/chunks/x.js', 'static/chunks/y.js'] });
  });

  it('passes within budget', () => {
    expect(check({ routes: { '/a': 100 * 1024 }, total: 200 * 1024 }, { routeFirstLoadKb: 180, totalKb: 280 })).toEqual([]);
  });

  it('flags a route and the total over budget', () => {
    const problems = check(
      { routes: { '/a': 181 * 1024, '/b': 10 * 1024 }, total: 281 * 1024 },
      { routeFirstLoadKb: 180, totalKb: 280 },
    );
    expect(problems).toHaveLength(2);
    expect(problems[0]).toContain('/a');
    expect(problems[1]).toContain('all client JS');
  });
});
