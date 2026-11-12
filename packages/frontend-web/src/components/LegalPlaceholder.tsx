import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/navigation';

export function LegalPlaceholder({ titleKey }: { titleKey: 'termsTitle' | 'privacyTitle' }) {
  const t = useTranslations('legal');
  return (
    <article className="card mx-auto max-w-2xl">
      <h1 className="text-2xl">{t(titleKey)}</h1>
      <p className="mt-3 text-muted">{t('preparing')}</p>
      <Link href="/" className="btn btn-ghost mt-5">
        {t('home')}
      </Link>
    </article>
  );
}
