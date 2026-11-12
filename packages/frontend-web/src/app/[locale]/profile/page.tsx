import { redirect } from '@/i18n/navigation';

// Google's "connect" flow returns to /profile; account settings live under /settings/security.
export default async function ProfilePage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  redirect({ href: '/settings/security', locale });
}
