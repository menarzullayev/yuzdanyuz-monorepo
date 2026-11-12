'use client';

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/navigation';
import { authApi } from '@/lib/api/auth';
import { useErrors } from '@/lib/use-errors';
import { AuthCard, ErrorBox } from './AuthCard';
import { Field } from './Field';

export function RecoverForm() {
  const t = useTranslations('recover');
  const errors = useErrors();
  const [username, setUsername] = useState('');
  const [contact, setContact] = useState('');
  const [error, setError] = useState('');
  const [sent, setSent] = useState<'telegram' | 'admin' | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (kind: 'telegram' | 'admin') => {
    setBusy(true);
    setError('');
    try {
      if (kind === 'telegram') await authApi.recoveryTelegram(username.trim().toLowerCase());
      else await authApi.recoveryRequest(username.trim().toLowerCase(), contact.trim());
      setSent(kind);
    } catch (e) {
      setError(errors.message(e));
    } finally {
      setBusy(false);
    }
  };

  const userField = (
    <Field label={t('username')}>
      {(p) => (
        <input
          {...p}
          className="field"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          required
        />
      )}
    </Field>
  );

  return (
    <AuthCard pose={error ? 'notogri-javob' : 'salom'} title={t('title')} subtitle={t('intro')}>
      {error && <ErrorBox>{error}</ErrorBox>}
      {sent && (
        <p role="status" className="rounded-xl border border-line p-3 text-sm">
          {sent === 'telegram' ? t('sent') : t('adminSent')}
        </p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run('telegram');
        }}
        className="space-y-4"
      >
        {userField}
        <button className="btn btn-primary w-full" disabled={busy || !username.trim()}>
          {busy ? t('sending') : t('telegramSend')}
        </button>
      </form>
      <hr className="border-line" />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run('admin');
        }}
        className="space-y-3"
      >
        <h2 className="text-base">{t('adminTitle')}</h2>
        <p className="text-sm text-muted">{t('adminHelp')}</p>
        <Field label={t('contact')}>
          {(p) => (
            <input
              {...p}
              className="field"
              value={contact}
              onChange={(e) => setContact(e.target.value)}
              autoComplete="tel"
              required
            />
          )}
        </Field>
        <button className="btn btn-ghost w-full" disabled={busy || !username.trim() || !contact.trim()}>
          {t('adminSend')}
        </button>
      </form>
      <p className="text-center">
        <Link href="/login" className="link inline-flex min-h-11 items-center">
          {t('backToLogin')}
        </Link>
      </p>
    </AuthCard>
  );
}
