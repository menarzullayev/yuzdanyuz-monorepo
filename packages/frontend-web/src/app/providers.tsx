'use client';

/**
 * Client providers — wraps app with TanStack Query + Sonner toast.
 *
 * Per Next 16 docs: keep QueryClient instance in state so Suspense + RSC don't
 * recreate it on every render. Avoid module-level singletons (breaks SSR).
 *
 * Global mutation error handler → toast (no per-mutation onError needed for
 * generic UX). Per-mutation `onError` still overrides for custom handling.
 */

import { useState, type ReactNode } from 'react';

import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { Toaster } from 'sonner';

import { APIError } from '@/api/error';
import { toast } from '@/lib/toast';

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30 * 1000, // 30 sec
            gcTime: 5 * 60 * 1000, // 5 min
            retry: (failureCount, error) => {
              // Skip 4xx — user/auth errors don't benefit from retry
              if (error instanceof APIError && error.status >= 400 && error.status < 500) {
                return false;
              }
              return failureCount < 2;
            },
            refetchOnWindowFocus: false,
          },
          mutations: {
            retry: false,
          },
        },
        mutationCache: new MutationCache({
          // Global mutation error handler — toast for unhandled errors.
          // Per-mutation `onError: (e) => { ... }` returning truthy disables this.
          onError: (error, _vars, _ctx, mutation) => {
            if (mutation.options.onError) return; // caller will handle
            toast.fromApiError(error);
          },
        }),
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      {children}
      <Toaster
        position="top-right"
        richColors
        closeButton
        toastOptions={{
          className: 'border-border',
        }}
      />
      {process.env.NODE_ENV === 'development' && <ReactQueryDevtools initialIsOpen={false} />}
    </QueryClientProvider>
  );
}
