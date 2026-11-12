import type { Metadata } from 'next';
import { Atkinson_Hyperlegible_Next, Manrope, Unbounded } from 'next/font/google';
import { cookies } from 'next/headers';
import { notFound } from 'next/navigation';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import '../globals.css';
import { SiteHeader } from '@/components/SiteHeader';
import { BRAND_NAME } from '@/config/brand';
import { alternates, LOCALE_LANG, LOCALES, type Locale } from '@/i18n/locales';
import { routing } from '@/i18n/routing';
import { htmlAttrs, parsePrefs, PREFS_COOKIE } from '@/lib/prefs';

const unbounded = Unbounded({
  subsets: ['latin', 'latin-ext', 'cyrillic'],
  weight: '800',
  variable: '--f-unbounded',
  display: 'swap',
});
const manrope = Manrope({
  subsets: ['latin', 'latin-ext', 'cyrillic'],
  variable: '--f-manrope',
  display: 'swap',
});
const atkinson = Atkinson_Hyperlegible_Next({
  subsets: ['latin', 'latin-ext'],
  variable: '--f-atkinson',
  display: 'swap',
});

export function generateStaticParams() {
  return LOCALES.map((locale) => ({ locale }));
}

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) return {};
  const t = await getTranslations({ locale, namespace: 'meta' });
  return {
    title: { default: t('title', { brand: BRAND_NAME }), template: `%s · ${BRAND_NAME}` },
    description: t('description', { brand: BRAND_NAME }),
    alternates: alternates(locale as Locale, '/'),
    other: { 'theme-color': '#0B7A5F' },
  };
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) notFound();
  setRequestLocale(locale);
  const prefs = parsePrefs((await cookies()).get(PREFS_COOKIE)?.value);
  const t = await getTranslations({ locale, namespace: 'header' });

  return (
    <html
      lang={LOCALE_LANG[locale as Locale]}
      className={`${unbounded.variable} ${manrope.variable} ${atkinson.variable}`}
      {...htmlAttrs(prefs)}
    >
      <body className="min-h-dvh font-sans antialiased">
        <NextIntlClientProvider>
          <a
            href="#main"
            className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-lg focus:bg-accent focus:px-4 focus:py-3 focus:text-black"
          >
            {t('skipToContent')}
          </a>
          <SiteHeader initialPrefs={prefs} />
          <main id="main" className="mx-auto w-full max-w-5xl px-4 pb-16 pt-4">
            {children}
          </main>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
