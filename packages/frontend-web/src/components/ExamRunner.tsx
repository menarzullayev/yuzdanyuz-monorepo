'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Link, useRouter } from '@/i18n/navigation';
import { ApiError } from '@/lib/api/errors';
import { examsApi, type Attempt, type StartPayload } from '@/lib/api/exams';
import {
  answersByQuestion,
  formatClock,
  isSupported,
  secondsLeft,
  toSelected,
  toggle,
  unansweredCount,
} from '@/lib/exam';
import { useErrors } from '@/lib/use-errors';
import { useMe } from '@/lib/use-me';

type SaveState = 'saving' | 'saved' | 'error';

const clock = () => Date.now();

export function ExamRunner({ mockId }: { mockId: string }) {
  const t = useTranslations('exam');
  const tc = useTranslations('common');
  const errors = useErrors();
  const router = useRouter();
  const { me } = useMe();

  const [data, setData] = useState<StartPayload | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [chosen, setChosen] = useState<Record<string, number[]>>({});
  const [saves, setSaves] = useState<Record<string, SaveState>>({});
  const [index, setIndex] = useState(0);
  const [left, setLeft] = useState<number | null>(null);
  const [strikes, setStrikes] = useState(0);
  const [ended, setEnded] = useState<Attempt['status'] | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [finishing, setFinishing] = useState(false);

  const received = useRef(0);
  const shownAt = useRef(0);
  // One request at a time per question, so a slow save can never overwrite a newer one.
  const chains = useRef<Record<string, Promise<void>>>({});
  const attemptId = data?.attempt.id;

  useEffect(() => {
    if (!me) return;
    let alive = true;
    examsApi
      .start(mockId)
      .then((payload) => {
        if (!alive) return;
        received.current = clock();
        shownAt.current = clock();
        setData(payload);
        setChosen(answersByQuestion(payload.answers));
        setStrikes(payload.attempt.strikes);
        setLeft(payload.attempt.seconds_left);
      })
      .catch((e) => alive && setError(e));
    return () => {
      alive = false;
    };
  }, [me, mockId]);

  const goToResult = useCallback(() => {
    if (attemptId) router.replace(`/exams/result/${attemptId}`);
  }, [attemptId, router]);

  const finish = useCallback(async () => {
    if (!attemptId || finishing) return;
    setFinishing(true);
    try {
      await Promise.allSettled(Object.values(chains.current));
      await examsApi.submit(attemptId);
    } catch {
      // The result page reads the attempt again and shows whatever the server decided.
    } finally {
      goToResult();
    }
  }, [attemptId, finishing, goToResult]);

  // Countdown from the server's seconds_left; at zero the attempt is handed in.
  useEffect(() => {
    if (!data || ended) return;
    const tick = () => {
      const s = secondsLeft(data.attempt.seconds_left, received.current, clock());
      setLeft(s);
      if (s === 0) void finish();
    };
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [data, ended, finish]);

  // Anti-cheat: leaving the tab or copying is reported; the server counts strikes.
  useEffect(() => {
    if (!attemptId || ended) return;
    const report = (event: 'tab_switch' | 'copy_attempt') =>
      examsApi
        .antiCheat(attemptId, event)
        .then((a) => {
          setStrikes(a.strikes);
          if (a.status !== 'in_progress') setEnded(a.status);
        })
        .catch(() => undefined);
    // A reload or closing the tab also hides the page; that is not switching tabs, and
    // the attempt resumes on the next load, so it must not cost the student a strike.
    let leaving = false;
    const onPageHide = () => {
      leaving = true;
    };
    const onVisibility = () => {
      if (document.visibilityState === 'hidden' && !leaving) void report('tab_switch');
    };
    const onCopy = (e: ClipboardEvent) => {
      e.preventDefault();
      void report('copy_attempt');
    };
    window.addEventListener('beforeunload', onPageHide);
    window.addEventListener('pagehide', onPageHide);
    document.addEventListener('visibilitychange', onVisibility);
    document.addEventListener('copy', onCopy);
    return () => {
      window.removeEventListener('beforeunload', onPageHide);
      window.removeEventListener('pagehide', onPageHide);
      document.removeEventListener('visibilitychange', onVisibility);
      document.removeEventListener('copy', onCopy);
    };
  }, [attemptId, ended]);

  const choose = (questionId: string, optionId: number) => {
    const q = data?.exam.questions.find((x) => x.question.id === questionId);
    if (!q || !attemptId || ended) return;
    const ids = toggle(q.question.type, chosen[questionId] ?? [], optionId);
    setChosen((c) => ({ ...c, [questionId]: ids }));
    const selected = toSelected(q.question.type, ids);
    if (selected === null) return;
    const spent = Math.round((clock() - shownAt.current) / 1000);
    setSaves((s) => ({ ...s, [questionId]: 'saving' }));
    const previous = chains.current[questionId] ?? Promise.resolve();
    chains.current[questionId] = previous.then(() =>
      examsApi
        .answer(attemptId, questionId, selected, spent)
        .then(() => setSaves((s) => ({ ...s, [questionId]: 'saved' })))
        .catch((e) => {
          if (e instanceof ApiError && e.code === 'attempt_expired') {
            setEnded('expired');
            goToResult();
            return;
          }
          setSaves((s) => ({ ...s, [questionId]: 'error' }));
        }),
    );
  };

  const go = (i: number) => {
    setIndex(i);
    shownAt.current = clock();
  };

  if (error) {
    return (
      <div className="mx-auto max-w-2xl space-y-3">
        <div role="alert" className="card border-danger">
          {errors.message(error)}
        </div>
        <Link href="/exams" className="link">
          {t('backToList')}
        </Link>
      </div>
    );
  }
  if (!data) return <p role="status">{tc('loading')}</p>;

  if (ended === 'cancelled') {
    return (
      <div className="mx-auto max-w-2xl space-y-3">
        <p role="alert" className="card border-danger">
          {t('cancelled')}
        </p>
        <Link href="/exams" className="link">
          {t('backToList')}
        </Link>
      </div>
    );
  }

  const questions = data.exam.questions;
  const current = questions[index];
  const qid = current?.question.id;
  const picked = (qid && chosen[qid]) || [];
  const save = qid ? saves[qid] : undefined;
  const supported = current ? isSupported(current.question.type) : false;
  const multi = current?.question.type === 'MC';
  const lowTime = left !== null && left <= 60;

  return (
    <div className="mx-auto max-w-3xl space-y-4" onContextMenu={(e) => e.preventDefault()}>
      <header className="card flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg">{data.exam.title}</h1>
        <p className={lowTime ? 'font-bold text-danger' : 'font-bold'}>
          <span className="sr-only">{t('timeLeft')}: </span>
          <span role="timer" aria-live="off">
            {formatClock(left ?? data.attempt.seconds_left)}
          </span>
        </p>
      </header>

      <p className="text-sm text-muted">{t('rules')}</p>
      {strikes > 0 && (
        <p role="alert" className="card border-danger">
          {t('strikeWarning', { strikes })}
        </p>
      )}

      {current && (
        <section className="card space-y-3" aria-labelledby="q-title">
          <p id="q-title" className="text-sm text-muted">
            {t('questionOf', { current: index + 1, total: questions.length })}
          </p>
          <p className="whitespace-pre-wrap text-lg select-none">{current.question.content.text ?? ''}</p>
          {supported ? (
            <fieldset className="space-y-2">
              <legend className="sr-only">{t('questionOf', { current: index + 1, total: questions.length })}</legend>
              {multi && <p className="text-sm text-muted">{t('multiHint')}</p>}
              {current.question.options.map((o) => {
                const on = picked.includes(o.id);
                return (
                  <label
                    key={o.id}
                    className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 select-none ${
                      on ? 'border-main' : 'border-line'
                    }`}
                  >
                    <input
                      type={multi ? 'checkbox' : 'radio'}
                      name={`q-${qid}`}
                      checked={on}
                      onChange={() => qid && choose(qid, o.id)}
                      className="mt-1"
                    />
                    <span className="whitespace-pre-wrap">{o.text}</span>
                  </label>
                );
              })}
            </fieldset>
          ) : (
            <p className="text-muted">{t('unsupported')}</p>
          )}
          <p aria-live="polite" className="min-h-5 text-sm">
            {save === 'saving' && t('saving')}
            {save === 'saved' && t('saved')}
            {save === 'error' && <span className="text-danger">{t('saveFailed')}</span>}
          </p>
          <div className="flex justify-between gap-3">
            <button type="button" className="btn btn-ghost" disabled={index === 0} onClick={() => go(index - 1)}>
              {t('prev')}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              disabled={index === questions.length - 1}
              onClick={() => go(index + 1)}
            >
              {t('next')}
            </button>
          </div>
        </section>
      )}

      <nav aria-label={t('navLabel')} className="card">
        <ol className="flex flex-wrap gap-2">
          {questions.map((q, i) => {
            const answered = (chosen[q.question.id]?.length ?? 0) > 0;
            return (
              <li key={q.question.id}>
                <button
                  type="button"
                  onClick={() => go(i)}
                  aria-current={i === index ? 'step' : undefined}
                  className={`h-10 w-10 rounded-lg border text-sm font-semibold ${
                    i === index ? 'border-main' : 'border-line'
                  } ${answered ? 'bg-main text-on-main' : ''}`}
                >
                  {i + 1}
                </button>
              </li>
            );
          })}
        </ol>
      </nav>

      {confirming ? (
        <div className="card space-y-3 border-accent" role="dialog" aria-modal="false" aria-labelledby="confirm-title">
          <h2 id="confirm-title" className="text-lg">
            {t('confirmTitle')}
          </h2>
          <p>{t('confirmBody', { count: unansweredCount(questions, chosen) })}</p>
          <div className="flex flex-wrap gap-3">
            <button type="button" className="btn btn-primary" disabled={finishing} onClick={() => void finish()}>
              {t('confirmYes')}
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setConfirming(false)}>
              {t('confirmNo')}
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="btn btn-primary" onClick={() => setConfirming(true)}>
          {t('finish')}
        </button>
      )}
    </div>
  );
}
