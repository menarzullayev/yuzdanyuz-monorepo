export const LOCALES = ['uz', 'ru', 'en', 'kaa', 'uz-Cyrl', 'uz-yangi'] as const;
export type Locale = (typeof LOCALES)[number];

// TODO(2027-04-22): the new Uzbek alphabet becomes the official default on that date.
// Then set DEFAULT_LOCALE = 'uz-yangi' (and keep 'uz' reachable under /uz). Do NOT switch before.
export const DEFAULT_LOCALE: Locale = 'uz';

/** Native names for the language switcher. */
export const LOCALE_LABELS: Record<Locale, string> = {
  uz: 'Oʻzbekcha',
  'uz-yangi': 'Özbekçe (yangi alifbo)',
  'uz-Cyrl': 'Ўзбекча',
  ru: 'Русский',
  en: 'English',
  kaa: 'Qaraqalpaqsha',
};

/** Value of <html lang> and hreflang. The new alphabet has no registered subtag: private use. */
export const LOCALE_LANG: Record<Locale, string> = {
  uz: 'uz',
  ru: 'ru',
  en: 'en',
  kaa: 'kaa',
  'uz-Cyrl': 'uz-Cyrl',
  'uz-yangi': 'uz-Latn-x-yangi',
};

export const isLocale = (v: string | undefined): v is Locale => !!v && (LOCALES as readonly string[]).includes(v);

/** Path of `pathname` (without locale prefix) in `locale`. */
export function localizedPath(locale: Locale, pathname: string): string {
  const p = pathname === '/' ? '' : pathname;
  return locale === DEFAULT_LOCALE ? p || '/' : `/${locale}${p}`;
}

/** Next metadata `alternates` for a locale-less pathname. */
export function alternates(locale: Locale, pathname: string) {
  return {
    canonical: localizedPath(locale, pathname),
    languages: {
      ...Object.fromEntries(LOCALES.map((l) => [LOCALE_LANG[l], localizedPath(l, pathname)])),
      'x-default': localizedPath(DEFAULT_LOCALE, pathname),
    },
  };
}
