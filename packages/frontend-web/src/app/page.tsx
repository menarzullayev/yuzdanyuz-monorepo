'use client';

import { useQuery } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';

import { api } from '@/api/client';
import { APIError } from '@/api/error';
import { Button } from '@/components/ui/button';

export default function Home() {
  const t = useTranslations();
  const health = useQuery({
    queryKey: ['health'],
    queryFn: () => api<{ status: string }>('/health/'),
  });

  return (
    <main className="bg-surface-app flex min-h-screen flex-col items-center justify-center gap-8 p-8">
      <h1 className="t-display-2">{t('common.brand')}</h1>
      <p className="t-caption">{t('home.smokeTest')}</p>

      <div className="bg-card border-border w-full max-w-md rounded-lg border p-6 shadow-sm">
        <h2 className="t-h4 mb-3">Backend health (TanStack Query + i18n)</h2>
        {health.isPending && <p className="t-body-sm text-fg-2">{t('common.loading')}</p>}
        {health.isError && (
          <p className="t-body-sm text-destructive">
            {t('common.error')}:{' '}
            {health.error instanceof APIError
              ? `${health.error.status} ${health.error.message}`
              : String(health.error)}
          </p>
        )}
        {health.isSuccess && (
          <p className="t-body text-yy-success-600">✓ {health.data.status}</p>
        )}
      </div>

      <div className="flex flex-wrap gap-3">
        <Button variant="default">{t('auth.login')}</Button>
        <Button variant="secondary">{t('common.save')}</Button>
        <Button variant="outline">{t('common.back')}</Button>
        <Button variant="ghost">{t('common.cancel')}</Button>
      </div>

      <p className="t-num text-fg-2">187 / 210 {t('result.score')}</p>
    </main>
  );
}
