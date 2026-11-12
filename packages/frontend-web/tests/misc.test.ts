import { describe, expect, it } from 'vitest';
import { alternates, localizedPath, LOCALES } from '../src/i18n/locales';
import { DEFAULT_PREFS, htmlAttrs, parsePrefs, serializePrefs } from '../src/lib/prefs';
import { safeNext } from '../src/lib/safe-next';
import { tokenFromHash } from '../src/lib/token';

describe('prefs cookie', () => {
  it('round-trips', () => {
    const p = { theme: 'dark', dyslexia: true, contrast: true, lite: true, fontSize: 130 } as const;
    expect(parsePrefs(serializePrefs(p))).toEqual(p);
    expect(serializePrefs(DEFAULT_PREFS)).toBe('');
  });
  it('ignores garbage', () => {
    expect(parsePrefs('t=neon&s=999&d=yes')).toEqual(DEFAULT_PREFS);
    expect(parsePrefs(undefined)).toEqual(DEFAULT_PREFS);
  });
  it('system theme writes no data-theme (CSS follows the OS)', () => {
    expect(htmlAttrs(DEFAULT_PREFS)).toEqual({ 'data-fs': '100' });
    expect(htmlAttrs({ ...DEFAULT_PREFS, theme: 'dark', lite: true })).toMatchObject({
      'data-theme': 'dark',
      'data-lite': '1',
    });
  });
});

describe('safeNext', () => {
  it('allows same-origin paths only', () => {
    expect(safeNext('/settings/security')).toBe('/settings/security');
    for (const bad of ['//evil.com', 'https://evil.com', '/\\evil', 'javascript:1', '', null, undefined]) {
      expect(safeNext(bad as string)).toBe('/dashboard');
    }
  });
});

describe('recovery token fragment', () => {
  it('reads #token=', () => {
    expect(tokenFromHash('#token=abc_DEF-1')).toBe('abc_DEF-1');
    expect(tokenFromHash('')).toBe('');
    expect(tokenFromHash('#other=1')).toBe('');
  });
});

describe('locales and hreflang', () => {
  it('default locale has no prefix, the others do', () => {
    expect(localizedPath('uz', '/login')).toBe('/login');
    expect(localizedPath('uz', '/')).toBe('/');
    expect(localizedPath('ru', '/login')).toBe('/ru/login');
    expect(localizedPath('uz-yangi', '/')).toBe('/uz-yangi');
  });
  it('alternates list every locale plus x-default', () => {
    const a = alternates('en', '/login');
    expect(Object.keys(a.languages)).toHaveLength(LOCALES.length + 1);
    expect(a.languages['x-default']).toBe('/login');
    expect(a.canonical).toBe('/en/login');
  });
});
