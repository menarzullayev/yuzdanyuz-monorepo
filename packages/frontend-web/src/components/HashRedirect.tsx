'use client';

import { useEffect } from 'react';
import { useRouter } from '@/i18n/navigation';

/** Client-side redirect that keeps the URL fragment (`#token=...`), which a server redirect would lose. */
export function HashRedirect({ to }: { to: string }) {
  const router = useRouter();
  useEffect(() => {
    router.replace(`${to}${window.location.hash}`);
  }, [router, to]);
  return null;
}
