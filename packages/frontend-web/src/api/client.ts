/**
 * API client — typed fetch wrapper with JWT cookie credentials.
 *
 * Pattern:
 *   const me = await api<{ id: string; phone: string }>('/api/auth/me/');
 *   await api('/api/auth/logout/', { method: 'POST' });
 *
 * For full type safety, use the path-typed helpers below:
 *   const stats = await apiGet('/api/affiliate/stats/');  // response type inferred from schema
 *
 * Cookie auth: `credentials: 'include'` ensures the access_token cookie set by
 * the backend (Path=/yuzdanyuz/, Secure, SameSite=Lax) is sent on every request.
 * The backend's TenantMiddleware extracts the current_org claim from the JWT.
 */

import type { paths } from './schema';

import { APIError } from './error';

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'https://hsm.sammu.uz/yuzdanyuz';

type Json = Record<string, unknown> | unknown[] | null;

/**
 * Low-level typed fetch. Use `apiGet`/`apiPost`/etc. for path-typed convenience.
 *
 * Throws `APIError` on non-2xx responses (carries status + parsed body for
 * the unified `{success:false, error:{...}}` envelope from ISSUE-204).
 */
export async function api<T = Json>(path: string, init?: RequestInit): Promise<T> {
  const url = path.startsWith('http') ? path : `${BASE_URL}${path}`;
  const res = await fetch(url, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // not JSON — swallow, body stays null
    }
    throw new APIError(res.status, res.statusText, body);
  }

  // 204 No Content
  if (res.status === 204) return undefined as T;

  // Plain text response (e.g. /ping/ → "pong")
  const contentType = res.headers.get('content-type') ?? '';
  if (!contentType.includes('json')) {
    return (await res.text()) as T;
  }

  return (await res.json()) as T;
}

// ── Path-typed convenience helpers ──────────────────────────────────────────
// These give full IntelliSense for the URL path (autocomplete from schema.ts)
// and infer response types from OpenAPI components.

type PathsGet = {
  [K in keyof paths as paths[K] extends { get: unknown } ? K : never]: paths[K];
};

type PathsPost = {
  [K in keyof paths as paths[K] extends { post: unknown } ? K : never]: paths[K];
};

// Response body type extractor (200 JSON content)
type ResponseOf<T> = T extends { responses: { 200: { content: { 'application/json': infer R } } } }
  ? R
  : T extends { responses: { 201: { content: { 'application/json': infer R } } } }
    ? R
    : unknown;

/**
 * Typed GET — path autocompletes, response type inferred from schema.
 *
 * Usage:
 *   const stats = await apiGet('/api/affiliate/stats/');
 *   // stats is typed as the schema's 200 response body
 */
export async function apiGet<P extends keyof PathsGet>(
  path: P,
  init?: Omit<RequestInit, 'method' | 'body'>,
): Promise<
  PathsGet[P] extends { get: infer G } ? ResponseOf<G> : never
> {
  return api(path as string, { ...init, method: 'GET' }) as Promise<
    PathsGet[P] extends { get: infer G } ? ResponseOf<G> : never
  >;
}

/**
 * Typed POST — path autocompletes, body + response type inferred.
 */
export async function apiPost<P extends keyof PathsPost>(
  path: P,
  body?: Json,
  init?: Omit<RequestInit, 'method' | 'body'>,
): Promise<
  PathsPost[P] extends { post: infer Pst } ? ResponseOf<Pst> : never
> {
  return api(path as string, {
    ...init,
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }) as Promise<PathsPost[P] extends { post: infer Pst } ? ResponseOf<Pst> : never>;
}

export { BASE_URL };
