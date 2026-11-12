'use client';

import { useTranslations } from 'next-intl';
import { errorText, fieldTexts } from '@/lib/api/errors';

/** Translated error helpers bound to the current locale. */
export function useErrors() {
  const t = useTranslations();
  const has = (k: string) => t.has(k as never);
  const tr = (k: string, v?: Record<string, string | number>) => t(k as never, v as never);
  return {
    message: (e: unknown) => errorText(e, tr, has),
    fields: (e: unknown) => fieldTexts(e, tr, has),
  };
}
