# frontend-web

Next.js 16 (App Router, React 19, Tailwind 4) web client: sign-in, registration, recovery,
2FA step, forced password change, onboarding and security settings.

## Run locally

```bash
npm ci                                   # at the repo root
cp packages/frontend-web/.env.example packages/frontend-web/.env.local
npm run dev -w @yuzdanyuz/frontend-web   # http://localhost:3000
```

| Env | Default | Meaning |
|---|---|---|
| `NEXT_PUBLIC_BRAND_NAME` | `mashqgoh` | Product name. Only `src/config/brand.ts` reads it; `npm run check:brand` fails if the name is hard-coded anywhere else in `src/` or `messages/`. |
| `API_URL` (or `NEXT_PUBLIC_API_URL`) | `http://127.0.0.1:8013` | Backend that `/api/*` and `/accounts/*` are proxied to (Next rewrites). |

The browser only talks to its own origin (`/api/auth/...`), so cookies are first-party and there is no CORS.
There is no CSRF token: the backend rejects state-changing requests whose `Origin` is not allowed, so add the
web origin (for example `http://localhost:3000`) to the backend `CORS_ALLOWED_ORIGINS` / `CSRF_TRUSTED_ORIGINS`
and set `FRONTEND_BASE_URL` to it. The backend recovery link points to `/reset-password#token=...`; that route
forwards (keeping the fragment) to `/recover/confirm`.

## Scripts

`npm run lint` · `npm run typecheck` · `npm test` (vitest) · `npm run build` · `npm run i18n:generate` · `npm run check:brand`

## Pages

`/login` (Telegram, Google, password, 2FA code, forced password change) · `/register` · `/recover` ·
`/recover/confirm` · `/onboarding` · `/settings/security` · `/dashboard` · `/terms` · `/privacy`
(placeholders). Locale prefixes: see `messages/README.md`.

## Look and accessibility

Palette "A · Zumrad" as CSS variables in `src/app/globals.css`, Unbounded 800 + Manrope (`next/font/google`).
Theme, dyslexia-friendly font (Atkinson Hyperlegible Next), high contrast, lite mode and font size 100/115/130%
live in the `prefs` cookie. The server renders them as `data-*` attributes on `<html>`, so there is no flash.
Without a stored theme the OS preference applies through `prefers-color-scheme`.
