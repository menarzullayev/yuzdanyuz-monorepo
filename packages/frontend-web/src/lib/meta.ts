import type { Metadata } from 'next';
import { hasLocale } from 'next-intl';
import { getTranslations } from 'next-intl/server';
import { alternates, type Locale } from '@/i18n/locales';
import { routing } from '@/i18n/routing';

/** Per-page title (from `<namespace>.<key>`) plus canonical and hreflang alternates. */
export async function pageMetadata(
  params: Promise<{ locale: string }>,
  namespace: string,
  key: string,
  path: string,
): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace });
  return { title: t(key), alternates: alternates(locale as Locale, path) };
}
