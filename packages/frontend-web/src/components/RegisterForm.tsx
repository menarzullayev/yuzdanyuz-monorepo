'use client';

import { useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { Link, useRouter } from '@/i18n/navigation';
import { authApi } from '@/lib/api/auth';
import { ApiError } from '@/lib/api/errors';
import { passwordValid } from '@/lib/password';
import { safeNext } from '@/lib/safe-next';
import { useErrors } from '@/lib/use-errors';
import { AuthCard, ErrorBox } from './AuthCard';
import { Field, PasswordInput } from './Field';
import { GoogleButton } from './GoogleButton';
import { PasswordHints } from './PasswordHints';
import { TelegramLogin } from './TelegramLogin';

const UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term', 'start_param', 'referrer'];

export function RegisterForm() {
  const t = useTranslations('register');
  const tl = useTranslations('login');
  const errors = useErrors();
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get('next'));

  const [first, setFirst] = useState('');
  const [last, setLast] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [pwFocused, setPwFocused] = useState(false);
  const [error, setError] = useState('');
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [usernameState, setUsernameState] = useState<'idle' | 'checking' | 'free'>('idle');
  const [busy, setBusy] = useState(false);

  // Debounced availability check: shows the server's suggestions when the name is taken.
  useEffect(() => {
    const name = username.trim().toLowerCase();
    if (name.length < 3) return;
    let cancelled = false;
    const id = window.setTimeout(async () => {
      setUsernameState('checking');
      try {
        const r = await authApi.usernameCheck(name);
        if (cancelled) return;
        if (r.available) {
          setUsernameState('free');
          setSuggestions([]);
          setFieldErr((f) => ({ ...f, username: '' }));
        } else {
          setUsernameState('idle');
          setSuggestions(r.suggestions ?? []);
          setFieldErr((f) => ({ ...f, username: errors.message(new ApiError(400, r.reason ?? 'username_invalid', '')) }));
        }
      } catch {
        if (!cancelled) setUsernameState('idle');
      }
    }, 450);
    return () => {
      cancelled = true;
      window.clearTimeout(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `errors` is recreated on every render
  }, [username]);

  const ctx = { username, firstName: first, lastName: last, email };
  const canSubmit = first.trim().length >= 2 && last.trim().length >= 2 && username.length >= 3 && passwordValid(password, ctx);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    setFieldErr({});
    const acquisition = Object.fromEntries(UTM_KEYS.flatMap((k) => (params.get(k) ? [[k, params.get(k)!]] : [])));
    try {
      await authApi.register({
        first_name: first.trim(),
        last_name: last.trim(),
        username: username.trim().toLowerCase(),
        password,
        remember: true,
        ...(email.trim() ? { email: email.trim() } : {}),
        ...(Object.keys(acquisition).length ? { acquisition } : {}),
      });
      router.replace(next);
    } catch (err) {
      setError(errors.message(err));
      setFieldErr(errors.fields(err));
      if (err instanceof ApiError && err.suggestions.length) setSuggestions(err.suggestions);
    } finally {
      setBusy(false);
    }
  };

  const usernameHelp =
    usernameState === 'checking' ? t('usernameChecking') : usernameState === 'free' ? t('usernameFree') : t('usernameHelp');

  return (
    <AuthCard pose={error ? 'notogri-javob' : pwFocused ? 'kozini-yopgan' : 'salom'} title={t('title')} subtitle={t('subtitle')}>
      <TelegramLogin remember onSuccess={() => router.replace(next)} />
      <GoogleButton label={tl('google')} />
      <div className="flex items-center gap-3 text-sm text-muted" role="separator">
        <span className="h-px flex-1 bg-line" />
        {tl('withPassword')}
        <span className="h-px flex-1 bg-line" />
      </div>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error && <ErrorBox>{error}</ErrorBox>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t('firstName')} error={fieldErr.first_name}>
            {(p) => (
              <input {...p} className="field" value={first} onChange={(e) => setFirst(e.target.value)} autoComplete="given-name" required />
            )}
          </Field>
          <Field label={t('lastName')} error={fieldErr.last_name}>
            {(p) => (
              <input {...p} className="field" value={last} onChange={(e) => setLast(e.target.value)} autoComplete="family-name" required />
            )}
          </Field>
        </div>
        <Field label={t('username')} error={fieldErr.username} help={usernameHelp}>
          {(p) => (
            <input
              {...p}
              className="field"
              value={username}
              onChange={(e) => {
                setUsername(e.target.value);
                setUsernameState('idle');
              }}
              autoComplete="username"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              required
            />
          )}
        </Field>
        {suggestions.length > 0 && (
          <div className="text-sm">
            <span className="mr-2 font-bold">{t('usernameSuggestions')}</span>
            {suggestions.map((s) => (
              <button
                key={s}
                type="button"
                className="btn btn-ghost mr-2 mt-1 text-sm"
                onClick={() => {
                  setUsername(s);
                  setSuggestions([]);
                  setFieldErr((f) => ({ ...f, username: '' }));
                }}
              >
                {s}
              </button>
            ))}
          </div>
        )}
        <Field label={t('password')} error={fieldErr.password}>
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
        <PasswordHints value={password} ctx={ctx} />
        <Field label={t('email')} optional error={fieldErr.email}>
          {(p) => (
            <input
              {...p}
              type="email"
              className="field"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              autoCapitalize="off"
              spellCheck={false}
            />
          )}
        </Field>
        <button className="btn btn-primary w-full" disabled={busy || !canSubmit}>
          {busy ? t('submitting') : t('submit')}
        </button>
        <p className="text-center text-sm text-muted">
          {t.rich('consent', {
            terms: (c) => (
              <Link href="/terms" className="link">
                {c}
              </Link>
            ),
            privacy: (c) => (
              <Link href="/privacy" className="link">
                {c}
              </Link>
            ),
          })}
        </p>
      </form>
      <p className="text-center text-sm">
        {t('haveAccount')}{' '}
        <Link href="/login" className="link">
          {t('login')}
        </Link>
      </p>
    </AuthCard>
  );
}
