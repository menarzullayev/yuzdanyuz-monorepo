'use client';

import { useEffect, useRef, useState } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import { Link, usePathname } from '@/i18n/navigation';
import { LOCALE_LABELS, LOCALES, type Locale } from '@/i18n/locales';
import {
  DEFAULT_PREFS,
  htmlAttrs,
  PREFS_COOKIE,
  serializePrefs,
  type FontSize,
  type Prefs,
  type ThemePref,
} from '@/lib/prefs';

const ATTRS = ['data-theme', 'data-font', 'data-contrast', 'data-lite', 'data-fs'];

function apply(p: Prefs) {
  const el = document.documentElement;
  ATTRS.forEach((a) => el.removeAttribute(a));
  Object.entries(htmlAttrs(p)).forEach(([k, v]) => el.setAttribute(k, v));
  document.cookie = `${PREFS_COOKIE}=${encodeURIComponent(serializePrefs(p))}; path=/; max-age=31536000; samesite=lax`;
}

export function PrefsMenu({ initial }: { initial: Prefs }) {
  const t = useTranslations('prefs');
  const locale = useLocale() as Locale;
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [prefs, setPrefs] = useState(initial);
  const root = useRef<HTMLDivElement>(null);
  const latest = useRef(initial); // survives several changes within one tick

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    const onClick = (e: MouseEvent) =>
      root.current && !root.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onClick);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onClick);
    };
  }, [open]);

  const update = (patch: Partial<Prefs>) => {
    const next = { ...latest.current, ...patch };
    latest.current = next;
    setPrefs(next);
    apply(next);
  };

  const themes: [ThemePref, string][] = [
    ['system', t('themeSystem')],
    ['light', t('themeLight')],
    ['dark', t('themeDark')],
  ];
  const sizes: FontSize[] = [100, 115, 130];
  const toggles = [
    ['dyslexia', t('dyslexia')],
    ['contrast', t('contrast')],
    ['lite', t('lite')],
  ] as const;

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        className="btn btn-ghost"
        aria-expanded={open}
        aria-controls="prefs-panel"
        onClick={() => setOpen((o) => !o)}
      >
        <span aria-hidden>Aa</span>
        <span className="sr-only">{t('title')}</span>
        <span aria-hidden className="hidden sm:inline">
          {LOCALE_LABELS[locale]}
        </span>
      </button>
      {open && (
        <div
          id="prefs-panel"
          role="dialog"
          aria-label={t('title')}
          className="card absolute right-0 z-40 mt-2 w-[min(22rem,calc(100vw-2rem))] space-y-4 shadow-lg"
        >
          <fieldset>
            <legend className="mb-1 text-sm font-bold">{t('language')}</legend>
            <ul className="grid grid-cols-2 gap-2">
              {LOCALES.map((l) => (
                <li key={l}>
                  <Link
                    href={pathname}
                    locale={l}
                    lang={l}
                    aria-current={l === locale ? 'true' : undefined}
                    className={`btn w-full text-sm ${l === locale ? 'btn-primary' : 'btn-ghost'}`}
                  >
                    {LOCALE_LABELS[l]}
                  </Link>
                </li>
              ))}
            </ul>
          </fieldset>
          <fieldset>
            <legend className="mb-1 text-sm font-bold">{t('theme')}</legend>
            <div className="flex flex-wrap gap-2">
              {themes.map(([v, label]) => (
                <label key={v} className={`btn text-sm ${prefs.theme === v ? 'btn-primary' : 'btn-ghost'}`}>
                  <input
                    type="radio"
                    name="theme"
                    className="sr-only"
                    checked={prefs.theme === v}
                    onChange={() => update({ theme: v })}
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend className="mb-1 text-sm font-bold">{t('fontSize')}</legend>
            <div className="flex gap-2">
              {sizes.map((s) => (
                <label key={s} className={`btn flex-1 text-sm ${prefs.fontSize === s ? 'btn-primary' : 'btn-ghost'}`}>
                  <input
                    type="radio"
                    name="fs"
                    className="sr-only"
                    checked={prefs.fontSize === s}
                    onChange={() => update({ fontSize: s })}
                  />
                  {s}%
                </label>
              ))}
            </div>
          </fieldset>
          {toggles.map(([k, label]) => (
            <label key={k} className="flex min-h-11 cursor-pointer items-center gap-3 text-sm">
              <input
                type="checkbox"
                className="size-5 accent-[var(--c-main)]"
                checked={prefs[k]}
                onChange={(e) => update({ [k]: e.target.checked })}
              />
              {label}
            </label>
          ))}
          <button
            type="button"
            className="btn btn-ghost w-full text-sm"
            onClick={() => {
              latest.current = DEFAULT_PREFS;
              setPrefs(DEFAULT_PREFS);
              apply(DEFAULT_PREFS);
            }}
          >
            {t('reset')}
          </button>
        </div>
      )}
    </div>
  );
}
