import { ApiError, networkError, parseError } from './errors';

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE';

interface Options {
  method?: Method;
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  /** Do not try refresh + retry on 401 (login, refresh itself, anonymous checks). */
  noRefresh?: boolean;
}

// Same-origin: next.config rewrites /api/* to the backend, so cookies are first-party and
// there is no CORS. There is no CSRF token to fetch: the backend relies on SameSite=Lax and
// rejects foreign Origin headers on state-changing requests (docs/auth-api.md).
const BASE = '/api/auth';

async function send(path: string, opts: Options): Promise<Response> {
  const url = new URL(BASE + path, 'http://local');
  for (const [k, v] of Object.entries(opts.query ?? {})) {
    if (v !== undefined) url.searchParams.set(k, String(v));
  }
  try {
    return await fetch(url.pathname + url.search, {
      method: opts.method ?? 'GET',
      credentials: 'include',
      headers: opts.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      cache: 'no-store',
    });
  } catch {
    throw networkError();
  }
}

let refreshing: Promise<boolean> | null = null;

/** One refresh at a time: parallel 401s share the same request. */
function refreshOnce(): Promise<boolean> {
  refreshing ??= send('/refresh/', { method: 'POST' })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  let res = await send(path, opts);
  if (res.status === 401 && !opts.noRefresh && (await refreshOnce())) {
    res = await send(path, opts);
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) throw parseError(res.status, body, res.headers.get('Retry-After'));
  return body as T;
}

export { ApiError };
