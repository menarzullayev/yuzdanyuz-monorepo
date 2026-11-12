import { useTranslations } from 'next-intl';
import { Mascot } from '@/components/Mascot';
import { Link } from '@/i18n/navigation';

export default function NotFound() {
  const t = useTranslations('notFound');
  return (
    <div className="mx-auto max-w-md text-center">
      <Mascot pose="xarita" size={180} />
      <h1 className="mt-2 text-2xl">{t('title')}</h1>
      <p className="mt-2 text-muted">{t('text')}</p>
      <Link href="/" className="btn btn-primary mt-5">
        {t('home')}
      </Link>
    </div>
  );
}
