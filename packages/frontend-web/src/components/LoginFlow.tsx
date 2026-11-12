'use client';

import { useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { Link, useRouter } from '@/i18n/navigation';
import { authApi } from '@/lib/api/auth';
import { passwordValid } from '@/lib/password';
import { safeNext } from '@/lib/safe-next';
import { useErrors } from '@/lib/use-errors';
import { AuthCard, ErrorBox } from './AuthCard';
import { Field, PasswordInput } from './Field';
import { GoogleButton } from './GoogleButton';
import { PasswordHints } from './PasswordHints';
import { TelegramLogin } from './TelegramLogin';

type Step = 'main' | 'code' | 'change';

export function LoginFlow() {
  const t = useTranslations('login');
  const errors = useErrors();
  const router = useRouter();
  const next = safeNext(useSearchParams().get('next'));

  const [step, setStep] = useState<Step>('main');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(true);
  const [code, setCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [changeToken, setChangeToken] = useState('');
  const [pwFocused, setPwFocused] = useState(false);
  const [error, setError] = useState('');
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const done = () => router.replace(next);

  const fail = (e: unknown) => {
    setError(errors.message(e));
    setFieldErr(errors.fields(e));
  };

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError('');
    setFieldErr({});
    try {
      await fn();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const submitCredentials = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const res = await authApi.login({
        username: username.trim(),
        password,
        remember,
        ...(step === 'code' ? { code: code.replace(/\s/g, '') } : {}),
      });
      setPwFocused(false); // the password field unmounts without a blur event
      if ('need_code' in res) setStep('code');
      else if ('password_change_required' in res) {
        setChangeToken(res.change_token);
        setStep('change');
      } else done();
    });
  };

  const submitChange = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await authApi.changeRequiredPassword({ change_token: changeToken, new_password: newPassword, remember });
      done();
    });
  };

  const pose = error ? 'notogri-javob' : pwFocused ? 'kozini-yopgan' : 'salom';

  if (step === 'change') {
    return (
      <AuthCard pose={pose} title={t('changeTitle')} subtitle={t('changeHelp')}>
        <form onSubmit={submitChange} className="space-y-4" noValidate>
          {error && <ErrorBox>{error}</ErrorBox>}
          <Field label={t('newPassword')} error={fieldErr.new_password}>
            {(p) => (
              <PasswordInput
                {...p}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                onFocus={() => setPwFocused(true)}
                onBlur={() => setPwFocused(false)}
                autoComplete="new-password"
                required
              />
            )}
          </Field>
          <PasswordHints value={newPassword} ctx={{ username }} />
          <button className="btn btn-primary w-full" disabled={busy || !passwordValid(newPassword, { username })}>
            {t('changeSubmit')}
          </button>
        </form>
      </AuthCard>
    );
  }

  if (step === 'code') {
    return (
      <AuthCard pose={pose} title={t('codeTitle')} subtitle={t('codeHelp')}>
        <form onSubmit={submitCredentials} className="space-y-4">
          {error && <ErrorBox>{error}</ErrorBox>}
          <Field label={t('codeLabel')}>
            {(p) => (
              <input
                {...p}
                className="field text-center tracking-widest"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode="text"
                autoComplete="one-time-code"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                autoFocus
                required
              />
            )}
          </Field>
          <button className="btn btn-primary w-full" disabled={busy || code.trim().length < 6}>
            {t('codeSubmit')}
          </button>
          <button
            type="button"
            className="btn btn-ghost w-full"
            onClick={() => {
              setStep('main');
              setCode('');
              setPassword('');
              setError('');
            }}
          >
            {t('codeBack')}
          </button>
        </form>
      </AuthCard>
    );
  }

  return (
    <AuthCard pose={pose} title={t('title')} subtitle={t('subtitle')}>
      <TelegramLogin remember={remember} onSuccess={done} />
      <GoogleButton />
      <div className="flex items-center gap-3 text-sm text-muted" role="separator">
        <span className="h-px flex-1 bg-line" />
        {t('withPassword')}
        <span className="h-px flex-1 bg-line" />
      </div>
      <form onSubmit={submitCredentials} className="space-y-4">
        {error && <ErrorBox>{error}</ErrorBox>}
        <Field label={t('username')} error={fieldErr.username}>
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
              inputMode="text"
              required
            />
          )}
        </Field>
        <Field label={t('password')} error={fieldErr.password}>
          {(p) => (
            <PasswordInput
              {...p}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onFocus={() => setPwFocused(true)}
              onBlur={() => setPwFocused(false)}
              autoComplete="current-password"
              required
            />
          )}
        </Field>
        <label className="flex min-h-11 cursor-pointer items-center gap-3">
          <input
            type="checkbox"
            className="size-5 accent-[var(--c-main)]"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
          />
          {t('remember')}
        </label>
        <button className="btn btn-primary w-full" disabled={busy || !username || !password}>
          {busy ? t('submitting') : t('submit')}
        </button>
      </form>
      <p className="text-center">
        <Link href="/recover" className="link inline-flex min-h-11 items-center">
          {t('forgot')}
        </Link>
      </p>
      <p className="text-center text-sm">
        {t('noAccount')}{' '}
        <Link href="/register" className="link">
          {t('register')}
        </Link>
      </p>
    </AuthCard>
  );
}
