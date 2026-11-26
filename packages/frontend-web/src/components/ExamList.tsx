'use client';

import { useEffect, useState } from 'react';
import { useFormatter, useTranslations } from 'next-intl';
import { Link } from '@/i18n/navigation';
import { examsApi, type MockExam } from '@/lib/api/exams';
import { isOpen } from '@/lib/exam';
import { useErrors } from '@/lib/use-errors';
import { useMe } from '@/lib/use-me';

export function ExamList() {
  const t = useTranslations('exam');
  const tc = useTranslations('common');
  const format = useFormatter();
  const errors = useErrors();
  const { me } = useMe();
  const [mocks, setMocks] = useState<MockExam[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!me) return;
    let alive = true;
    examsApi
      .mocks()
      .then((list) => alive && setMocks(list))
      .catch((e) => alive && setError(e));
    const tick = setInterval(() => setNow(Date.now()), 30_000);
    return () => {
      alive = false;
      clearInterval(tick);
    };
  }, [me]);

  const when = (iso: string) => format.dateTime(new Date(iso), { dateStyle: 'medium', timeStyle: 'short' });

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl">{t('title')}</h1>
        <Link href="/certificates" className="link">
          {t('certificates')}
        </Link>
      </div>
      {error ? (
        <div role="alert" className="card border-danger">
          {errors.message(error)}
        </div>
      ) : mocks === null ? (
        <p role="status">{tc('loading')}</p>
      ) : mocks.length === 0 ? (
        <p className="card">{t('empty')}</p>
      ) : (
        <ul className="space-y-3">
          {mocks.map((m) => {
            const open = isOpen(m.scheduled_at, m.closes_at, now);
            return (
              <li key={m.id} className="card space-y-2">
                <h2 className="text-lg">{m.title}</h2>
                {m.description && <p className="whitespace-pre-line text-muted">{m.description}</p>}
                <p className="text-sm text-muted">
                  {t('minutes', { count: m.duration_minutes })} · {t('questions', { count: m.question_count })} ·{' '}
                  {open ? t('closesAt', { date: when(m.closes_at) }) : t('opensAt', { date: when(m.scheduled_at) })}
                </p>
                {open && (
                  <Link href={`/exams/${m.id}`} className="btn btn-primary">
                    {t('start')}
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
