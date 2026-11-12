export type ThemePref = 'system' | 'light' | 'dark';
export type FontSize = 100 | 115 | 130;

export interface Prefs {
  theme: ThemePref;
  dyslexia: boolean;
  contrast: boolean;
  lite: boolean;
  fontSize: FontSize;
}

export const PREFS_COOKIE = 'prefs';
export const DEFAULT_PREFS: Prefs = {
  theme: 'system',
  dyslexia: false,
  contrast: false,
  lite: false,
  fontSize: 100,
};

/** Cookie value looks like `t=dark&d=1&c=1&l=1&s=115`; unknown or broken parts fall back to defaults. */
export function parsePrefs(raw: string | undefined | null): Prefs {
  const out: Prefs = { ...DEFAULT_PREFS };
  if (!raw) return out;
  const p = new URLSearchParams(raw);
  const t = p.get('t');
  if (t === 'light' || t === 'dark') out.theme = t;
  out.dyslexia = p.get('d') === '1';
  out.contrast = p.get('c') === '1';
  out.lite = p.get('l') === '1';
  const s = Number(p.get('s'));
  if (s === 115 || s === 130) out.fontSize = s;
  return out;
}

export function serializePrefs(p: Prefs): string {
  const q = new URLSearchParams();
  if (p.theme !== 'system') q.set('t', p.theme);
  if (p.dyslexia) q.set('d', '1');
  if (p.contrast) q.set('c', '1');
  if (p.lite) q.set('l', '1');
  if (p.fontSize !== 100) q.set('s', String(p.fontSize));
  return q.toString();
}

/** Attributes written on <html>; the server renders them so there is no flash. */
export function htmlAttrs(p: Prefs): Record<string, string> {
  const a: Record<string, string> = { 'data-fs': String(p.fontSize) };
  if (p.theme !== 'system') a['data-theme'] = p.theme;
  if (p.dyslexia) a['data-font'] = 'dyslexia';
  if (p.contrast) a['data-contrast'] = 'high';
  if (p.lite) a['data-lite'] = '1';
  return a;
}
