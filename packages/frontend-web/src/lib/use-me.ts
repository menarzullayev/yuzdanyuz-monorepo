'use client';

import { useCallback, useEffect, useState } from 'react';
import { usePathname, useRouter } from '@/i18n/navigation';
import { authApi } from '@/lib/api/auth';
import { ApiError } from '@/lib/api/errors';
import type { Me } from '@/lib/api/types';

/**
 * Loads the signed-in user. Cookies are HttpOnly, so the only way to know is to ask /me/.
 * With `require`, an anonymous visitor is sent to /login and brought back afterwards.
 */
export function useMe({ require = true }: { require?: boolean } = {}) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setMe(await authApi.me());
      setError(null);
    } catch (e) {
      setMe(null);
      if (e instanceof ApiError && e.status === 401) {
        if (require) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      } else setError(e);
    } finally {
      setLoading(false);
    }
  }, [require, router, pathname]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch on mount
    void load();
  }, [load]);

  return { me, loading, error, reload: load };
}
