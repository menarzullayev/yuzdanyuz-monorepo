'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { authApi } from '@/lib/api/auth';
import type { User } from '@/lib/api/types';
import { useErrors } from '@/lib/use-errors';
import { useTgPoll } from '@/lib/use-tg-poll';
import { TelegramIcon } from './TelegramIcon';

type Phase = 'idle' | 'starting' | 'waiting' | 'completing' | 'expired';

/** Official Telegram blue button + deeplink flow: init -> open bot -> poll status -> complete. */
export function TelegramLogin({ remember, onSuccess }: { remember: boolean; onSuccess: (u: User) => void }) {
  const t = useTranslations('login');
  const errors = useErrors();
  const [phase, setPhase] = useState<Phase>('idle');
  const [token, setToken] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState('');

  const reset = (next: Phase = 'idle') => {
    setToken(null);
    setLink(null);
    setPhase(next);
  };

  const start = async () => {
    setError('');
    setPhase('starting');
    try {
      const init = await authApi.tgInit();
      setToken(init.token);
      setLink(init.deep_link);
      setPhase('waiting');
      window.open(init.deep_link, '_blank', 'noopener');
    } catch (e) {
      setError(errors.message(e));
      reset();
    }
  };

  useTgPoll(phase === 'waiting' ? token : null, async (status) => {
    if (status === 'expired') {
      reset('expired');
      return true;
    }
    if (status !== 'verified' || !token) return false;
    setPhase('completing');
    try {
      const { user } = await authApi.tgComplete(token, remember);
      onSuccess(user);
    } catch (e) {
      setError(errors.message(e));
      reset();
    }
    return true;
  });

  const busy = phase === 'starting' || phase === 'completing';

  return (
    <div>
      <button type="button" className="btn btn-telegram w-full" onClick={start} disabled={busy || phase === 'waiting'}>
        <TelegramIcon />
        {t('telegram')}
      </button>
      {phase === 'waiting' && (
        <div className="mt-3 rounded-xl border border-line p-3 text-sm" role="status">
          <p>{t('telegramWaiting')}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {link && (
              <a className="btn btn-ghost text-sm" href={link} target="_blank" rel="noopener noreferrer">
                {t('telegramOpen')}
              </a>
            )}
            <button type="button" className="btn btn-ghost text-sm" onClick={() => reset()}>
              {t('telegramCancel')}
            </button>
          </div>
        </div>
      )}
      {phase === 'completing' && (
        <p className="mt-2 text-sm" role="status">
          {t('telegramWaitingConfirm')}
        </p>
      )}
      {phase === 'expired' && (
        <p className="mt-2 text-sm font-semibold text-danger" role="alert">
          {t('telegramExpired')}
        </p>
      )}
      {error && (
        <p className="mt-2 text-sm font-semibold text-danger" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
