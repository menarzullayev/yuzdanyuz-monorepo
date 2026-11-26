/**
 * next-intl request config — server-side locale resolution.
 *
 * For MVP, locale is derived from:
 *   1. NEXT_PUBLIC_DEFAULT_LOCALE env var (default: uz-Latn)
 *   2. Future: cookie `locale` set by user picker, or accept-language header
 *
 * Step 15 ships single-locale default. Per-user locale switching is Phase 4 (Step 59).
 */

import { getRequestConfig } from 'next-intl/server';

import { defaultLocale, locales, type Locale } from './config';

export default getRequestConfig(async () => {
  const envLocale = process.env.NEXT_PUBLIC_DEFAULT_LOCALE as Locale | undefined;
  const locale = envLocale && locales.includes(envLocale) ? envLocale : defaultLocale;

  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
