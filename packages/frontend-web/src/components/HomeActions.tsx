'use client';

import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/navigation';
import { useMe } from '@/lib/use-me';

export function HomeActions() {
  const t = useTranslations('home');
  const { me, loading } = useMe({ require: false });
  return (
    <div className="mt-6 flex flex-wrap justify-center gap-3" aria-busy={loading}>
      {me ? (
        <Link href="/dashboard" className="btn btn-primary">
          {t('dashboard')}
        </Link>
      ) : (
        <>
          <Link href="/register" className="btn btn-primary">
            {t('register')}
          </Link>
          <Link href="/login" className="btn btn-ghost">
            {t('login')}
          </Link>
        </>
      )}
    </div>
  );
}
