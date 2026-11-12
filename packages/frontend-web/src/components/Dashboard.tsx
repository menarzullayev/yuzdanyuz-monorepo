'use client';

import { useTranslations } from 'next-intl';
import { Link, useRouter } from '@/i18n/navigation';
import { authApi } from '@/lib/api/auth';
import { useMe } from '@/lib/use-me';

export function Dashboard() {
  const t = useTranslations('dashboard');
  const tc = useTranslations('common');
  const router = useRouter();
  const { me, loading } = useMe();
  if (loading || !me) return <p role="status">{tc('loading')}</p>;

  const logout = async () => {
    try {
      await authApi.logout();
    } finally {
      router.replace('/login');
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="text-2xl">{t('welcome', { name: me.first_name || me.display_name })}</h1>
      {!me.onboarding.is_complete && (
        <div className="card flex flex-wrap items-center justify-between gap-3 border-accent">
          <p>{t('onboardingBanner')}</p>
          <Link href="/onboarding" className="btn btn-primary">
            {t('onboardingCta')}
          </Link>
        </div>
      )}
      <div className="flex flex-wrap gap-3">
        <Link href="/settings/security" className="btn btn-ghost">
          {t('security')}
        </Link>
        <button type="button" className="btn btn-ghost" onClick={logout}>
          {t('logout')}
        </button>
      </div>
    </div>
  );
}
