'use client';

import { useEffect, useRef } from 'react';
import { authApi } from '@/lib/api/auth';
import type { TgStatus } from '@/lib/api/types';

/**
 * Polls /tg/status/ every 2 s while `token` is set. `onStatus` returns true to stop polling.
 * Stops by itself when the token expires (5 min on the server) or the component unmounts.
 */
export function useTgPoll(token: string | null, onStatus: (s: TgStatus) => boolean | Promise<boolean>) {
  const cb = useRef(onStatus);
  useEffect(() => {
    cb.current = onStatus;
  });

  useEffect(() => {
    if (!token) return;
    let stopped = false;
    let busy = false;
    const tick = async () => {
      if (busy || stopped) return;
      busy = true;
      try {
        const { status } = await authApi.tgStatus(token);
        if (await cb.current(status)) stopped = true;
      } catch {
        // transient network error: keep polling until the token expires
      } finally {
        busy = false;
      }
    };
    const id = window.setInterval(() => {
      if (stopped) window.clearInterval(id);
      else void tick();
    }, 2000);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [token]);
}
