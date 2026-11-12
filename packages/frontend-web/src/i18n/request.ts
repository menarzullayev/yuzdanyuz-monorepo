import { getRequestConfig } from 'next-intl/server';
import { isLocale } from './locales';
import { routing } from './routing';

export default getRequestConfig(async ({ requestLocale }) => {
  const asked = await requestLocale;
  const locale = isLocale(asked) ? asked : routing.defaultLocale;
  return { locale, messages: (await import(`../../messages/${locale}.json`)).default };
});
