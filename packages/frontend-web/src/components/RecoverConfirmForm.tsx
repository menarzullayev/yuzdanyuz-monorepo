'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/navigation';
import { authApi } from '@/lib/api/auth';
import { passwordValid } from '@/lib/password';
import { tokenFromHash } from '@/lib/token';
import { useErrors } from '@/lib/use-errors';
import { AuthCard, ErrorBox } from './AuthCard';
import { Field, PasswordInput } from './Field';
import { PasswordHints } from './PasswordHints';

export function RecoverConfirmForm() {
  const t = useTranslations('recoverConfirm');
  const errors = useErrors();
  const [token, setToken] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [pwFocused, setPwFocused] = useState(false);
  const [error, setError] = useState('');
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the fragment exists only in the browser
    setToken(tokenFromHash(window.location.hash));
    // Drop the secret from the address bar and history right away.
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  }, []);

  if (token === null) return null;

  if (done) {
    return (
      <AuthCard pose="konfetti" title={t('title')}>
        <p role="status">{t('done')}</p>
        <Link href="/login" className="btn btn-primary w-full">
          {t('goLogin')}
        </Link>
      </AuthCard>
    );
  }

  if (!token) {
    return (
      <AuthCard pose="xarita" title={t('title')}>
        <ErrorBox>{t('missingToken')}</ErrorBox>
        <Link href="/recover" className="btn btn-primary w-full">
          {t('restart')}
        </Link>
      </AuthCard>
    );
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    setFieldErr({});
    try {
      await authApi.recoveryConfirm(token, password);
      setDone(true);
    } catch (err) {
      setError(errors.message(err));
      setFieldErr(errors.fields(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthCard pose={error ? 'notogri-javob' : pwFocused ? 'kozini-yopgan' : 'salom'} title={t('title')} subtitle={t('help')}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error && <ErrorBox>{error}</ErrorBox>}
        <Field label={t('newPassword')} error={fieldErr.new_password}>
          {(p) => (
            <PasswordInput
              {...p}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onFocus={() => setPwFocused(true)}
              onBlur={() => setPwFocused(false)}
              autoComplete="new-password"
              required
            />
          )}
        </Field>
        <PasswordHints value={password} />
        <button className="btn btn-primary w-full" disabled={busy || !passwordValid(password)}>
          {t('submit')}
        </button>
      </form>
    </AuthCard>
  );
}
