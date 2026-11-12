import { useTranslations } from 'next-intl';

/** Full-page navigation: allauth on the backend handles the OAuth dance and sets the cookies. */
export function GoogleButton({ label }: { label?: string }) {
  const t = useTranslations('login');
  return (
    // eslint-disable-next-line @next/next/no-html-link-for-pages -- full-page navigation to the backend OAuth view
    <a className="btn btn-ghost w-full" href="/accounts/google/login/">
      <svg aria-hidden width="20" height="20" viewBox="0 0 48 48">
        <path fill="#4285F4" d="M45.1 24.5c0-1.6-.1-3.1-.4-4.5H24v8.5h11.9c-.5 2.8-2.1 5.1-4.4 6.7v5.5h7.1c4.2-3.8 6.5-9.5 6.5-16.2Z" />
        <path fill="#34A853" d="M24 46c6 0 11-2 14.6-5.4l-7.1-5.5c-2 1.3-4.5 2.1-7.5 2.1-5.8 0-10.7-3.9-12.4-9.2H4.3v5.7C7.9 41.1 15.4 46 24 46Z" />
        <path fill="#FBBC05" d="M11.6 28c-.4-1.3-.7-2.7-.7-4.2s.3-2.9.7-4.2v-5.7H4.3C2.8 16.9 2 20.3 2 24s.8 7.1 2.3 10.1l7.3-6.1Z" />
        <path fill="#EA4335" d="M24 10.6c3.3 0 6.2 1.1 8.5 3.3l6.4-6.4C35 4.1 30 2 24 2 15.4 2 7.9 6.9 4.3 14.1l7.3 5.7C13.3 14.5 18.2 10.6 24 10.6Z" />
      </svg>
      {label ?? t('google')}
    </a>
  );
}
