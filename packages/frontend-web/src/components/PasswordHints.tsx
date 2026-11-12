'use client';

import { useTranslations } from 'next-intl';
import { checkPassword, strength, type PasswordContext } from '@/lib/password';

const STRENGTH_KEY = { weak: 'strengthWeak', ok: 'strengthOk', strong: 'strengthStrong' } as const;

export function PasswordHints({ value, ctx }: { value: string; ctx?: PasswordContext }) {
  const t = useTranslations('password');
  const hints = checkPassword(value, ctx);
  const s = strength(value, ctx);
  const touched = value.length > 0;
  return (
    <div className="mt-2 text-sm" aria-live="polite">
      <p className="mb-1 flex items-center justify-between font-bold">
        <span>{t('hintsTitle')}</span>
        {touched && (
          <span className={s === 'weak' ? 'text-danger' : 'text-main'}>{t(STRENGTH_KEY[s])}</span>
        )}
      </p>
      <ul className="space-y-1">
        {hints.map((h) => (
          <li key={h.id} className={touched ? (h.ok ? 'text-main' : 'text-danger') : 'text-muted'}>
            <span aria-hidden>{touched ? (h.ok ? '✓ ' : '✗ ') : '• '}</span>
            {t(h.id)}
            <span className="sr-only">{touched ? ` (${h.ok ? t('met') : t('unmet')})` : ''}</span>
          </li>
        ))}
        <li className="text-muted">
          <span aria-hidden>• </span>
          {t('common')}
        </li>
      </ul>
    </div>
  );
}
