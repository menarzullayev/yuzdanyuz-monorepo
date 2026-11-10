'use client';

import { useSyncExternalStore } from 'react';

// Cookies expose no change event, so there is nothing to subscribe to.
const subscribe = () => () => {};

function hasAccessToken(): boolean {
  const token = document.cookie
    .split('; ')
    .find((row) => row.startsWith('access_token='))
    ?.split('=')[1];
  return !!token;
}

export function useAuth() {
  // The server snapshot is null: the cookie is unknown until the client hydrates.
  const authenticated = useSyncExternalStore<boolean | null>(subscribe, hasAccessToken, () => null);

  return { isAuthenticated: authenticated === true, isLoading: authenticated === null };
}
