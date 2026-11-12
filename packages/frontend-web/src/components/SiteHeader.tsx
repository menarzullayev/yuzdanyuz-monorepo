'use client';

import { useTranslations } from 'next-intl';
import { BRAND_NAME } from '@/config/brand';
import { Link } from '@/i18n/navigation';
import type { Prefs } from '@/lib/prefs';
import { PrefsMenu } from './PrefsMenu';

export function SiteHeader({ initialPrefs }: { initialPrefs: Prefs }) {
  const t = useTranslations('header');
  return (
    <header className="mx-auto flex w-full max-w-5xl items-center justify-between gap-3 px-4 py-3">
      <Link href="/" aria-label={t('home')} className="inline-flex min-h-11 items-center font-display text-xl text-main">
        {BRAND_NAME}
      </Link>
      <PrefsMenu initial={initialPrefs} />
    </header>
  );
}
