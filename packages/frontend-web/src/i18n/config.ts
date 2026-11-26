/**
 * i18n configuration — supported locales + defaults.
 *
 * Backend confirms uz-Latn is primary; uz-Cyrl, ru, en are co-equal options.
 * Per Claude Design rules: never hardcode strings — always use `t('key')`.
 *
 * Locale codes follow BCP 47:
 *   - uz-Latn — Uzbek Latin (primary)
 *   - uz-Cyrl — Uzbek Cyrillic
 *   - ru      — Russian
 *   - en      — English (marketing + B2B admin export)
 */

export const locales = ['uz-Latn', 'uz-Cyrl', 'ru', 'en'] as const;
export type Locale = (typeof locales)[number];

export const defaultLocale: Locale = 'uz-Latn';

export const localeNames: Record<Locale, string> = {
  'uz-Latn': "O'zbekcha",
  'uz-Cyrl': 'Ўзбекча',
  ru: 'Русский',
  en: 'English',
};
