# Authentication — Single Source of Truth

> **Status**: 2026-05-16 — REST + WebSocket cookie auth, SSE migration planned (see [websocket_strategy.md](./websocket_strategy.md))
>
> **Audience**: Frontend engineers (web + mobile), DevOps, security reviewers.

---

## 1. JWT cookie contract

All authenticated traffic — REST, WebSocket, future SSE — carries the JWT as
an **HttpOnly Secure cookie**. Frontend code never touches the token directly.

| Cookie | Purpose | TTL | HttpOnly | Secure | SameSite |
|---|---|---|---|---|---|
| `access_token` | API + WS auth | 15 min | ✅ | ✅ (prod) | `Lax` |
| `refresh_token` | rotate `access_token` | 14 days | ✅ | ✅ (prod) | `Lax` |

**Set on**: every successful login (`/api/auth/email/`, `/api/auth/otp/verify/`, `/api/auth/telegram/`, `/api/auth/tg/complete/`, allauth Google/Yandex/Apple OAuth callback) and every refresh (`/api/auth/refresh/`).

**Cleared on**: `/api/auth/logout/`.

### Token payload (HS256, signed by `SECRET_KEY`)

```jsonc
{
  "sub": "<user uuid>",          // CustomUser.id
  "fp":  "<sha256 fingerprint>", // browser+OS+IP — DeviceCheckMiddleware enforces
  "org": "<org uuid|null>",      // current_org claim — TenantMiddleware reads this
  "exp": 1747400000,             // unix timestamp
  "iat": 1747399100,
  "typ": "access" | "refresh"
}
```

`org` is set from the user's primary membership at login, or by the org-switch
endpoint. `TenantMiddleware` uses it to scope every queryset (`set_current_org`)
and `RLSMiddleware` mirrors it onto the PostgreSQL session
(`SET app.current_org_id`).

---

## 2. REST (HTTP) authentication

### Cookie + CORS

Frontend uses `fetch(url, { credentials: 'include' })` so the browser ships the
`access_token` cookie cross-origin.

For the browser to honor this, the server must reply with:
```
Access-Control-Allow-Origin: <exact frontend origin>   ← never '*'
Access-Control-Allow-Credentials: true
```

Configured via `django-cors-headers`:

| Setting | Dev | Prod |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000`, `http://127.0.0.1:3000` | env `CORS_ALLOWED_ORIGINS=` (comma) |
| `CORS_ALLOW_CREDENTIALS` | `True` | `True` |
| `CSRF_TRUSTED_ORIGINS` | dev origins + `https://hsm.sammu.uz` | env-extendable, default `https://hsm.sammu.uz` |

Dev also enables `CORS_ALLOWED_ORIGIN_REGEXES` for any `localhost:*` port
(Vite, Expo dev server).

### Auth flow

```
1. POST /api/auth/email/      body {email, password}
   ← 200 Set-Cookie: access_token=...; refresh_token=...
2. GET  /api/auth/me/         (cookie auto-sent)
   ← 200 {id, display_name, user_type, primary_organization, ...}
3. <any protected endpoint>   (cookie auto-sent)
4. (access_token expires)
5. POST /api/auth/refresh/    (refresh_token cookie auto-sent)
   ← 200 Set-Cookie: access_token=NEW; refresh_token=NEW   (rotated)
6. POST /api/auth/logout/
   ← 200 Set-Cookie: access_token=; refresh_token=;        (cleared)
```

### Middleware chain (request lifecycle)

```
SecurityMiddleware
WhiteNoiseMiddleware
CorsMiddleware              ← adds Access-Control-* headers
SessionMiddleware
CommonMiddleware
CsrfViewMiddleware
AuthenticationMiddleware
JWTAuthMiddleware           ← reads access_token cookie → request.user
DeviceCheckMiddleware       ← fingerprint match — else force-logout
AccountMiddleware           ← allauth
JWTCookieWriterMiddleware   ← allauth login → JWT cookie bridge
…
TenantMiddleware            ← request.user → org context
RLSMiddleware               ← org context → PostgreSQL SET app.current_org_id
RateLimitMiddleware         ← Redis sliding-window
```

DRF views use `SessionAuthentication` (default) + `IsAuthenticated` — by the
time a view runs, `request.user` is already populated by `JWTAuthMiddleware`,
so DRF just trusts it.

### Current user endpoint

```http
GET /api/auth/me/
Cookie: access_token=…

200 OK
{
  "id": "…",
  "email": "…",
  "phone_number": null,
  "display_name": "Aziz Karimov",
  "preferred_lang": "uz",
  "user_type": "student",
  "is_profile_complete": true,
  "telegram": null,
  "primary_organization": {"id": "…", "name": "Toshkent IT Park", "slug": "tit"},
  "is_staff": false,
  "is_superuser": false
}

401 Unauthorized   (no/invalid/expired cookie)
```

---

## 3. WebSocket authentication

> Today's behavior. SSE migration plan: [websocket_strategy.md](./websocket_strategy.md).

WS connections are authenticated **the same way as REST** — the browser sends
the `access_token` cookie automatically when it opens a WebSocket on the same
origin. The consumer re-verifies the token in `connect()` (defense-in-depth —
does not trust scope `user` set by middleware).

### Client (browser)

```ts
// Same origin as backend: cookies travel for free.
const ws = new WebSocket(
  `${BACKEND_HTTPS_ORIGIN.replace('https', 'wss')}/ws/exams/attempt/${attemptId}/`
);
// No subprotocol, no Authorization header, no query string token —
// the HttpOnly access_token cookie does the auth.
```

### Cross-origin caveat

The standard `WebSocket` constructor **does not accept a `credentials` option**;
the browser only sends cookies if the WS URL is **same-origin** with the page,
**or** if the cookies have `SameSite=None; Secure` AND the server replies with
a permissive `Sec-WebSocket-Origin` / CORS preflight on the underlying upgrade.

This is why production hosts the backend at the **same registrable domain** as
the frontend in deployment. For local dev, see "Dev gotchas" below.

### Server (`core/ws_auth.py`)

`ExamAttemptConsumer.connect()` calls `authenticate_ws(scope)`:

1. Parses the `Cookie:` header from ASGI scope headers.
2. Extracts `access_token`.
3. Calls `verify_access_token()` (same signature/expiry checks as REST).
4. Loads `CustomUser` by `payload['sub']`.
5. Falls back to `scope['user']` only if Channels' `AuthMiddlewareStack`
   populated it (used by tests).
6. Returns `None` → consumer closes with code **4401**.

### Close codes

| Code | Meaning |
|---|---|
| `4401` | unauthorized — no/invalid/expired cookie |
| `4404` | attempt not found or does not belong to user |
| `4409` | attempt status ≠ `in_progress` |
| `1000` | normal close — frontend reconnects with same cookie |

### Refresh on expiry

WebSocket connections do **not** auto-refresh. When `access_token` expires
mid-attempt, the server will reject the next reconnect with 4401. The frontend
should:
1. Detect 4401 close.
2. POST `/api/auth/refresh/` (rotates cookies).
3. Reconnect WebSocket — new cookie is sent automatically.

---

## 4. SSE authentication (planned)

Same as REST: `EventSource` ships cookies same-origin, and supports
`withCredentials: true` for cross-origin (Chromium ≥ 46, Firefox ≥ 11, Safari).

```ts
const sse = new EventSource(
  `${API_BASE}/api/v1/exams/${attemptId}/stream/`,
  { withCredentials: true }
);
sse.addEventListener('timer', (e) => { … });
sse.addEventListener('strike', (e) => { … });
```

SSE auth uses the same `access_token` cookie. View pattern: a long-running
`StreamingHttpResponse` that calls `request.user` exactly like any DRF view.
See `websocket_strategy.md` for the full migration design.

---

## 5. Dev gotchas

### Frontend on `localhost:3000`, backend on `localhost:8001`
Different origins. Configured in `dev.py`:
- `CORS_ALLOWED_ORIGINS = ['http://localhost:3000', 'http://127.0.0.1:3000']`
- `CORS_ALLOWED_ORIGIN_REGEXES = [r'^http://localhost:\d+$', …]`
- `CSRF_TRUSTED_ORIGINS` includes the same origins.
- `SESSION_COOKIE_SECURE = False`, `CSRF_COOKIE_SECURE = False` so cookies
  work over plain HTTP in dev.

Run frontend with the backend URL as `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8001`
(matching what we whitelisted) and `fetch(..., { credentials: 'include' })`.

### Frontend `localhost:3000`, backend over HTTPS (`https://hsm.sammu.uz/yuzdanyuz`)
Add the dev frontend origin to the prod env:

```bash
# .env on the prod host
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
CSRF_TRUSTED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

Browser **will refuse** to send a `Secure` cookie back to `http://localhost`
unless it was set with `SameSite=None; Secure` — and even then, only if the
top-level page is HTTPS. In practice: pointing dev frontend at prod backend
requires either using `localhost` with HTTPS (mkcert) or testing against the
deployed frontend.

### WebSocket in dev
The WebSocket consumer needs Daphne (ASGI), not gunicorn (WSGI):

```bash
daphne -p 8001 core.asgi:application
```

Pure gunicorn will accept HTTP fine but reject the `Upgrade: websocket`
handshake. `make run` uses Daphne by default.

---

## 6. Production caveat — PHP proxy

In the current HestiaCP subpath deployment, WebSocket upgrades **do not work**
through the PHP cURL proxy. WS endpoints respond 500 in production today; this
is why the SSE migration is a blocker for shipping real-time features. See
[websocket_strategy.md](./websocket_strategy.md).

REST endpoints (including `/api/auth/me/`, `/api/auth/refresh/`,
`/api/auth/logout/`) work normally through the proxy.

---

## 7. Frontend cookbook (Next.js + TanStack Query)

```ts
// src/api/client.ts
const BASE = process.env.NEXT_PUBLIC_API_BASE_URL!;

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: 'include',           // ← critical
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (res.status === 401) throw new APIError(401, 'Unauthorized');
  if (!res.ok) throw new APIError(res.status, await res.text());
  return res.json();
}

// src/hooks/useMe.ts
export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: () => api<MePayload>('/api/auth/me/'),
    staleTime: 5 * 60 * 1000,
    retry: (n, err) => !(err instanceof APIError && err.status === 401),
  });
}
```

On 401, the client should attempt a single refresh, then redirect to `/login`:

```ts
queryClient.setMutationDefaults(['refresh'], {
  mutationFn: () => api('/api/auth/refresh/', { method: 'POST' }),
});
```
