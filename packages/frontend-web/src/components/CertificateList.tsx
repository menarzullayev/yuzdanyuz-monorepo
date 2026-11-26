'use client';

import { useEffect, useState } from 'react';
import { useFormatter, useTranslations } from 'next-intl';
import { Link } from '@/i18n/navigation';
import { ApiError } from '@/lib/api/errors';
import { examsApi, type Certificate } from '@/lib/api/exams';
import { useErrors } from '@/lib/use-errors';
import { useMe } from '@/lib/use-me';

export function CertificateList() {
  const t = useTranslations('exam');
  const tc = useTranslations('common');
  const format = useFormatter();
  const errors = useErrors();
  const { me } = useMe();
  const [certs, setCerts] = useState<Certificate[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!me) return;
    let alive = true;
    examsApi
      .certificates()
      .then((list) => alive && setCerts(list))
      .catch((e) => alive && setError(e));
    return () => {
      alive = false;
    };
  }, [me]);

  const day = (iso: string) => format.dateTime(new Date(iso), { dateStyle: 'medium' });

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="text-2xl">{t('certificates')}</h1>
      {error instanceof ApiError && error.code === 'onboarding_required' ? (
        <div className="card space-y-3 border-accent">
          <p>{t('onboardingNeeded')}</p>
          <Link href="/onboarding" className="btn btn-primary">
            {t('onboardingCta')}
          </Link>
        </div>
      ) : error ? (
        <div role="alert" className="card border-danger">
          {errors.message(error)}
        </div>
      ) : certs === null ? (
        <p role="status">{tc('loading')}</p>
      ) : certs.length === 0 ? (
        <p className="card">{t('certificatesEmpty')}</p>
      ) : (
        <ul className="space-y-3">
          {certs.map((c) => (
            <li key={c.id} className="card space-y-1">
              <p className="font-semibold">{t('certNumber', { number: c.number })}</p>
              <p>{t('certGrade', { grade: c.grade })}</p>
              <p>{t('certScore', { score: c.scaled_score })}</p>
              {c.revoked_at ? (
                <p className="text-danger">{t('certRevoked')}</p>
              ) : (
                c.valid_until && <p className="text-muted">{t('certValid', { date: day(c.valid_until) })}</p>
              )}
            </li>
          ))}
        </ul>
      )}
      <Link href="/exams" className="link">
        {t('backToList')}
      </Link>
    </div>
  );
}
