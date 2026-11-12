import { useTranslations } from 'next-intl';
import { HomeActions } from '@/components/HomeActions';
import { Mascot } from '@/components/Mascot';

export default function HomePage() {
  const t = useTranslations('home');
  return (
    <section className="mx-auto max-w-2xl py-8 text-center">
      <Mascot pose="salom" size={200} />
      <h1 className="mt-4 text-3xl sm:text-4xl">{t('title')}</h1>
      <p className="mt-3 text-lg text-muted">{t('subtitle')}</p>
      <HomeActions />
    </section>
  );
}
