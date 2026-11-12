'use client';

import { useCallback, useEffect, useState } from 'react';
import qrcode from 'qrcode-generator';
import { useLocale, useTranslations } from 'next-intl';
import { authApi } from '@/lib/api/auth';
import type { Linked, SessionInfo, TgInit, TwoFactorState } from '@/lib/api/types';
import { useErrors } from '@/lib/use-errors';
import { useMe } from '@/lib/use-me';
import { useTgPoll } from '@/lib/use-tg-poll';
import { ErrorBox } from './AuthCard';
import { Field, PasswordInput } from './Field';
import { TelegramIcon } from './TelegramIcon';

function formatDate(value: string, locale: string): string {
  const d = new Date(value);
  // uz/kaa/Cyrillic data is patchy in browser ICU builds ("2026 M11 12"): use ru or en-GB layouts.
  for (const l of [locale === 'ru' ? 'ru' : 'en-GB']) {
    try {
      return new Intl.DateTimeFormat(l, { dateStyle: 'medium', timeStyle: 'short' }).format(d);
    } catch {
      // locale not supported by this ICU build: try the next one
    }
  }
  return value;
}

/** otpauth:// URI as an inline SVG QR. Always black on white so scanners work in dark mode. */
export function QrSvg({ value, label }: { value: string; label: string }) {
  const qr = qrcode(0, 'M');
  qr.addData(value);
  qr.make();
  const svg = qr.createSvgTag({ scalable: true, margin: 0 });
  return (
    <div
      role="img"
      aria-label={label}
      className="mx-auto w-48 rounded-xl bg-white p-3 [&_svg]:h-auto [&_svg]:w-full"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

function Sessions() {
  const t = useTranslations('security');
  const tc = useTranslations('common');
  const locale = useLocale();
  const errors = useErrors();
  const [list, setList] = useState<SessionInfo[] | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setList((await authApi.sessions()).sessions);
    } catch (e) {
      setError(errors.message(e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount
    void load();
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setError('');
    try {
      await fn();
      await load();
    } catch (e) {
      setError(errors.message(e));
    }
  };

  return (
    <section className="card space-y-3" aria-labelledby="sec-sessions">
      <h2 id="sec-sessions" className="text-lg">{t('sessionsTitle')}</h2>
      <p className="text-sm text-muted">{t('sessionsHelp')}</p>
      {error && <ErrorBox>{error}</ErrorBox>}
      {!list && <p role="status">{tc('loading')}</p>}
      <ul className="space-y-2">
        {list?.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-line p-3">
            <div className="min-w-0 text-sm">
              <p className="font-bold">
                {s.device_label}
                {s.is_current && <span className="ml-2 rounded-full bg-accent px-2 py-0.5 text-xs text-black">{t('current')}</span>}
              </p>
              <p className="text-muted">
                {[s.city, s.country].filter(Boolean).join(', ')} {s.ip_address ? `· ${s.ip_address}` : ''}
              </p>
              <p className="text-muted">
                {t('lastUsed')}: {formatDate(s.last_used_at, locale)}
              </p>
            </div>
            {!s.is_current && (
              <button type="button" className="btn btn-danger text-sm" onClick={() => act(() => authApi.revokeSession(s.id))}>
                {t('revoke')}
              </button>
            )}
          </li>
        ))}
      </ul>
      {list && list.length > 1 && (
        <button type="button" className="btn btn-ghost" onClick={() => act(authApi.revokeOthers)}>
          {t('revokeOthers')}
        </button>
      )}
    </section>
  );
}

function BackupCodes({ codes, onSaved }: { codes: string[]; onSaved: () => void }) {
  const t = useTranslations('security');
  const tc = useTranslations('common');
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-3 rounded-xl border border-line p-3">
      <h3 className="text-base">{t('backupTitle')}</h3>
      <p className="text-sm text-muted">{t('backupHelp')}</p>
      <ul className="grid grid-cols-2 gap-2 font-mono text-sm" aria-label={t('backupTitle')}>
        {codes.map((c) => (
          <li key={c} className="rounded-lg bg-bg px-2 py-2 text-center">{c}</li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="btn btn-ghost"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(codes.join('\n'));
              setCopied(true);
            } catch {
              // clipboard blocked: the codes stay visible to copy by hand
            }
          }}
        >
          {copied ? tc('copied') : tc('copy')}
        </button>
        <button type="button" className="btn btn-primary" onClick={onSaved}>
          {t('backupSaved')}
        </button>
      </div>
    </div>
  );
}

function TwoFactor({ hasPassword }: { hasPassword: boolean }) {
  const t = useTranslations('security');
  const tc = useTranslations('common');
  const errors = useErrors();
  const [state, setState] = useState<TwoFactorState | null>(null);
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [disabling, setDisabling] = useState(false);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setState(await authApi.twoFactor());
    } catch (e) {
      setError(errors.message(e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount
    void load();
  }, [load]);

  const act = async (fn: () => Promise<void>) => {
    setError('');
    try {
      await fn();
    } catch (e) {
      setError(errors.message(e));
    }
  };

  const codeField = (label: string) => (
    <Field label={label}>
      {(p) => (
        <input
          {...p}
          className="field tracking-widest"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          inputMode="text"
          autoComplete="one-time-code"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
        />
      )}
    </Field>
  );

  return (
    <section className="card space-y-3" aria-labelledby="sec-2fa">
      <h2 id="sec-2fa" className="text-lg">{t('twofaTitle')}</h2>
      {error && <ErrorBox>{error}</ErrorBox>}
      {codes && (
        <BackupCodes
          codes={codes}
          onSaved={() => {
            setCodes(null);
            void load();
          }}
        />
      )}
      {state && !state.enabled && !setup && (
        <>
          <p className="text-sm text-muted">{t('twofaOff')}</p>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => act(async () => setSetup(await authApi.twoFactorSetup()))}
          >
            {t('twofaEnable')}
          </button>
        </>
      )}
      {setup && (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            void act(async () => {
              const r = await authApi.twoFactorConfirm(code.replace(/\s/g, ''));
              setSetup(null);
              setCode('');
              setCodes(r.backup_codes);
            });
          }}
        >
          <p className="text-sm">{t('twofaScan')}</p>
          <QrSvg value={setup.otpauth_uri} label={t('twofaScan')} />
          <p className="text-sm text-muted">{t('twofaManual')}</p>
          <p className="break-all rounded-lg bg-bg p-2 text-center font-mono text-sm select-all">{setup.secret}</p>
          {codeField(t('twofaCode'))}
          <button className="btn btn-primary w-full" disabled={code.replace(/\s/g, '').length < 6}>
            {t('twofaConfirm')}
          </button>
        </form>
      )}
      {state?.enabled && !codes && (
        <>
          <p className="text-sm text-muted">{t('twofaOn', { count: state.backup_codes_remaining })}</p>
          {!disabling && (
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn btn-ghost" onClick={() => setDisabling(true)}>
                {t('twofaDisable')}
              </button>
            </div>
          )}
          {codeField(disabling ? t('disableCode') : t('twofaCode'))}
          {!disabling && (
            <button
              type="button"
              className="btn btn-ghost"
              disabled={code.trim().length < 6}
              onClick={() =>
                act(async () => {
                  setCodes((await authApi.backupCodes(code.replace(/\s/g, ''))).backup_codes);
                  setCode('');
                })
              }
            >
              {t('backupRegenerate')}
            </button>
          )}
          {disabling && (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                void act(async () => {
                  await authApi.twoFactorDisable({ ...(hasPassword ? { password } : {}), code: code.replace(/\s/g, '') });
                  setDisabling(false);
                  setCode('');
                  setPassword('');
                  await load();
                });
              }}
            >
              <p className="text-sm text-muted">{t('twofaDisableHelp')}</p>
              {hasPassword && (
                <Field label={t('disablePassword')}>
                  {(p) => <PasswordInput {...p} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />}
                </Field>
              )}
              <div className="flex gap-2">
                <button className="btn btn-danger" disabled={code.trim().length < 6 || (hasPassword && !password)}>
                  {t('disableConfirm')}
                </button>
                <button type="button" className="btn btn-ghost" onClick={() => setDisabling(false)}>
                  {tc('cancel')}
                </button>
              </div>
            </form>
          )}
        </>
      )}
    </section>
  );
}

function LinkedMethods() {
  const t = useTranslations('security');
  const errors = useErrors();
  const [linked, setLinked] = useState<Linked | null>(null);
  const [init, setInit] = useState<TgInit | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setLinked(await authApi.linked());
    } catch (e) {
      setError(errors.message(e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount
    void load();
  }, [load]);

  useTgPoll(init?.token ?? null, async (status) => {
    if (status === 'linked') {
      setInit(null);
      setNote('');
      await load();
      return true;
    }
    if (status === 'conflict') {
      setInit(null);
      setError(t('telegramLinkConflict'));
      return true;
    }
    if (status === 'expired') {
      setInit(null);
      setNote('');
      return true;
    }
    return false;
  });

  const act = async (fn: () => Promise<void>) => {
    setError('');
    try {
      await fn();
    } catch (e) {
      setError(errors.message(e));
    }
  };

  if (!linked) return null;
  const row = (
    key: 'telegram' | 'google',
    label: string,
    detail: string | null,
    isLinked: boolean,
    actions: React.ReactNode,
  ) => (
    <li key={key} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-line p-3">
      <div className="text-sm">
        <p className="font-bold">{label}</p>
        <p className="text-muted">{isLinked ? `${t('linked')}${detail ? ` · ${detail}` : ''}` : t('notLinked')}</p>
      </div>
      {actions}
    </li>
  );

  const unlinkBtn = (p: 'telegram' | 'google') => (
    <button
      type="button"
      className="btn btn-danger text-sm"
      disabled={!linked.can_unlink[p]}
      title={!linked.can_unlink[p] ? t('lastMethodHint') : undefined}
      onClick={() => act(async () => setLinked(await authApi.unlink(p)))}
    >
      {t('unlink')}
    </button>
  );

  return (
    <section className="card space-y-3" aria-labelledby="sec-linked">
      <h2 id="sec-linked" className="text-lg">{t('linkedTitle')}</h2>
      {error && <ErrorBox>{error}</ErrorBox>}
      <ul className="space-y-2">
        {row(
          'telegram',
          t('telegram'),
          linked.telegram.username ? `@${linked.telegram.username}` : null,
          linked.telegram.linked,
          linked.telegram.linked ? (
            unlinkBtn('telegram')
          ) : (
            <button
              type="button"
              className="btn btn-telegram text-sm"
              disabled={!!init}
              onClick={() =>
                act(async () => {
                  const r = await authApi.linkTelegramStart();
                  setInit(r);
                  setNote(t('telegramLinkWait'));
                  window.open(r.deep_link, '_blank', 'noopener');
                })
              }
            >
              <TelegramIcon size={18} />
              {t('link')}
            </button>
          ),
        )}
        {row(
          'google',
          t('google'),
          linked.google.email,
          linked.google.linked,
          linked.google.linked ? (
            unlinkBtn('google')
          ) : (
            <a className="btn btn-ghost text-sm" href={linked.google_connect_url}>
              {t('link')}
            </a>
          ),
        )}
      </ul>
      {note && (
        <p role="status" className="text-sm">
          {note} {init && <a className="link" href={init.deep_link} target="_blank" rel="noopener noreferrer">Telegram</a>}
        </p>
      )}
    </section>
  );
}

export function SecuritySettings() {
  const t = useTranslations('security');
  const tc = useTranslations('common');
  const { me, loading } = useMe();
  if (loading || !me) return <p role="status">{tc('loading')}</p>;
  return (
    <div className="mx-auto w-full max-w-2xl space-y-4">
      <h1 className="text-2xl">{t('title')}</h1>
      <Sessions />
      <TwoFactor hasPassword={me.has_password} />
      <LinkedMethods />
    </div>
  );
}
