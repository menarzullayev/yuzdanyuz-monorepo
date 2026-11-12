import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { generated } from '../scripts/generate-locales.mjs';
import { LOCALES } from '../src/i18n/locales';

const dir = join(__dirname, '..', 'messages');
const load = (l: string) => JSON.parse(readFileSync(join(dir, `${l}.json`), 'utf8'));

function flat(o: unknown, prefix = ''): Record<string, string> {
  if (typeof o === 'string') return { [prefix]: o };
  return Object.entries(o as object).reduce(
    (a, [k, v]) => ({ ...a, ...flat(v, prefix ? `${prefix}.${k}` : k) }),
    {},
  );
}
const args = (s: string) =>
  [...s.matchAll(/\{(\w+)/g)]
    .map((m) => m[1])
    .sort()
    .join(',');
const tags = (s: string) =>
  [...s.matchAll(/<\/?(\w+)>/g)]
    .map((m) => m[1])
    .sort()
    .join(',');

const uz = flat(load('uz'));

describe('message catalogs', () => {
  it('there is a catalog file for every locale', () => {
    for (const l of LOCALES) expect(() => load(l)).not.toThrow();
  });

  it.each(LOCALES.filter((l) => l !== 'uz'))(
    '%s has exactly the keys of uz, with the same ICU arguments and tags',
    (l) => {
      const c = flat(load(l));
      expect(Object.keys(c).sort()).toEqual(Object.keys(uz).sort());
      for (const k of Object.keys(uz)) {
        expect(args(c[k]), `${l}:${k} args`).toBe(args(uz[k]));
        expect(tags(c[k]), `${l}:${k} tags`).toBe(tags(uz[k]));
      }
    },
  );

  it('uz text uses U+02BB / U+02BC, never an ASCII apostrophe (it is also the ICU escape)', () => {
    expect(Object.entries(uz).filter(([, v]) => v.includes("'"))).toEqual([]);
  });

  it.each(['ru', 'en', 'kaa'])('%s has no ASCII apostrophe in ICU strings', (l) => {
    expect(Object.entries(flat(load(l))).filter(([, v]) => v.includes("'"))).toEqual([]);
  });

  it.each(['uz-Cyrl', 'uz-yangi'])(
    '%s is exactly what the generator produces from uz.json (run npm run i18n:generate)',
    (l) => {
      expect(load(l)).toEqual((generated as Record<string, unknown>)[l]);
    },
  );

  it('NEGATIVE: a hand-edited generated catalog is detected as drift', () => {
    const drifted = { ...load('uz-yangi'), meta: { title: 'edited by hand', description: '' } };
    expect(drifted).not.toEqual(generated['uz-yangi']);
  });

  it('the new alphabet has no leftover o-okina / g-okina / sh / ch outside ICU and tags', () => {
    const text = Object.values(flat(load('uz-yangi')))
      .join(' ')
      .replace(/\{[^}]*\}|<[^>]*>/g, '');
    expect(text).not.toMatch(/[oOgG]ʻ/);
    expect(text).not.toMatch(/sh|ch/i);
  });
});
