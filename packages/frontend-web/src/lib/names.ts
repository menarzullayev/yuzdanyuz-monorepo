import { toCyrillic, toNewAlphabet } from '../../scripts/translit.mjs';
import type { Locale } from '@/i18n/locales';

interface Named {
  name_uz: string;
  name_ru?: string;
  name_en?: string;
}

/** Reference data from the API only has uz/ru/en names; the other Uzbek scripts are derived from name_uz. */
export function localName(item: Named, locale: Locale): string {
  if (locale === 'ru') return item.name_ru || item.name_uz;
  if (locale === 'en') return item.name_en || item.name_uz;
  if (locale === 'uz-Cyrl') return toCyrillic(item.name_uz);
  if (locale === 'uz-yangi') return toNewAlphabet(item.name_uz);
  return item.name_uz;
}
