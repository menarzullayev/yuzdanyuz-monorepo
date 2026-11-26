'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/navigation';
import { ApiError } from '@/lib/api/errors';
import { examsApi, type Attempt } from '@/lib/api/exams';
import { useErrors } from '@/lib/use-errors';
import { useMe } from '@/lib/use-me';

// Scoring runs in a background task right after submit; poll briefly until it lands.
const POLL_MS = 2000;
const POLL_TRIES = 20;

export function ExamResult({ attemptId }: { attemptId: string }) {
  const t = useTranslations('exam');
  const tc = useTranslations('common');
  const errors = useErrors();
  const { me } = useMe();
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!me) return;
    let alive = true;
    let tries = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () =>
      examsApi
        .attempt(attemptId)
        .then((a) => {
          if (!alive) return;
          setAttempt(a);
          const pending = a.status !== 'in_progress' && a.status !== 'cancelled' && a.score === null;
          if (pending && ++tries < POLL_TRIES) timer = setTimeout(load, POLL_MS);
        })
        .catch((e) => alive && setError(e));
    void load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [me, attemptId]);

  const back = (
    <Link href="/exams" className="link">
      {t('backToList')}
    </Link>
  );

  if (error instanceof ApiError && error.code === 'onboarding_required') {
    return (
      <div className="mx-auto max-w-2xl space-y-3">
        <div className="card space-y-3 border-accent">
          <p>{t('onboardingNeeded')}</p>
          <Link href="/onboarding" className="btn btn-primary">
            {t('onboardingCta')}
          </Link>
        </div>
        {back}
      </div>
    );
  }
  if (error) {
    return (
      <div className="mx-auto max-w-2xl space-y-3">
        <div role="alert" className="card border-danger">
          {errors.message(error)}
        </div>
        {back}
      </div>
    );
  }
  if (!attempt) return <p role="status">{tc('loading')}</p>;

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="text-2xl">{t('resultTitle')}</h1>
      <div className="card space-y-2">
        <h2 className="text-lg">{attempt.exam_title}</h2>
        {attempt.status === 'cancelled' && <p className="text-danger">{t('cancelled')}</p>}
        {attempt.status === 'expired' && <p>{t('expired')}</p>}
        {attempt.status === 'in_progress' ? (
          <Link href={`/exams/${attempt.exam}`} className="btn btn-primary">
            {t('start')}
          </Link>
        ) : attempt.score === null ? (
          <p role="status">{t('scoring')}</p>
        ) : (
          <>
            <p className="text-4xl font-bold" aria-live="polite">
              {t('score', { score: Number(attempt.score).toLocaleString(undefined, { maximumFractionDigits: 2 }) })}
            </p>
            <p>{t('correct', { count: attempt.correct_count ?? 0 })}</p>
            <p>{t('points', { points: attempt.total_points ?? 0 })}</p>
          </>
        )}
      </div>
      {back}
    </div>
  );
}
