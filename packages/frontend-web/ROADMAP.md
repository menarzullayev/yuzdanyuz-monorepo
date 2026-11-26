# YuzDanYuz Frontend — 100-Step Strategic Roadmap

> **Author**: CTO-level strategic plan
> **Stack**: Next.js 16 + TypeScript + shadcn/ui + TanStack Query + Tailwind CSS v4
> **Backend**: `https://hsm.sammu.uz/yuzdanyuz/` (live, production-grade)
> **Design**: Claude Design v1 (registered as `yuzdanyuz-design` skill)
> **Target launch**: 2026-09 (soft launch) → 2026-11 (public launch)
> **Owner**: @narzullayevme + AI agents (Claude Code primary)

> ⚠️ **Next.js 16 NOTE**: Per `AGENTS.md` — Next.js 16 has breaking changes from
> training data. Read `node_modules/next/dist/docs/` BEFORE implementing
> routing/RSC/middleware code.

---

## 📊 Phase Map

```
PHASE 0 — Foundation              Steps 1-15    [2 hafta]   🔧 Infra + tooling
PHASE 1 — Auth flow               Steps 16-25   [1 hafta]   🔐 Login/signup/JWT
PHASE 2 — Layout shell            Steps 26-35   [1 hafta]   🏗  Nav, layout, skeletons
PHASE 3 — Student core            Steps 36-50   [3 hafta]   📝 Exam taking flow (MVP critical)
PHASE 4 — Engagement              Steps 51-60   [2 hafta]   🎯 Streak, league, leaderboard
PHASE 5 — Commerce                Steps 61-70   [2 hafta]   💳 Wallet, subscription, checkout
PHASE 6 — Intelligence            Steps 71-78   [2 hafta]   🧠 AI tutor, skill profile
PHASE 7 — B2B admin               Steps 79-88   [3 hafta]   📊 Org admin, analytics
PHASE 8 — Marketing site          Steps 89-92   [1 hafta]   📢 Landing, pricing
PHASE 9 — Mobile (React Native)   Steps 93-96   [3 hafta]   📱 Expo, student MVP
PHASE 10 — Polish & launch        Steps 97-100  [2 hafta]   🚀 a11y, perf, i18n QA

TOTAL: ~22 weeks (5.5 months) single dev, AI-assisted
       MVP (Phase 0-4 only): ~9 weeks → soft launch acceptable
```

---

## 🎯 Strategic principles

1. **MVP critical path** = Phase 0 + 1 + 2 + 3 (+ Phase 4 lite)
2. **Design system** = `yuzdanyuz-design` skill, single source of truth
3. **Type safety** = OpenAPI schema → TypeScript types (auto on each deploy)
4. **No premature features** — SAML/Bulk/multi-region defer to Phase 7+
5. **i18n birinchi kundan** — har string `t('...')`, hardcoded text reject
6. **Mobile-first design, mobile-last implementation**
7. **AI workflow** — Claude Code (impl) + yuzdanyuz-design (UI) + v0 (tweaks)
8. **Priority** — 🔴 MUST / 🟡 SHOULD / 🟢 NICE / ⚪ SKIP

---

# PHASE 0 — Foundation (2 hafta)

> **Definition of Done**: `npm run dev` ishlaydi, `/me` endpoint chaqiriladi, login page placeholder mavjud.

## Step 1: ✅ Monorepo workspace verification [DONE 2026-05-16]

- **Goal**: `packages/frontend-web/` mavjud, `package.json` workspace member
- **Acceptance**: `npm install` top-level'da ishlaydi (393 paket, 0 error)
- **Result**: ✅ `@yuzdanyuz/frontend-web@1.0.0` registered as workspace
- **Note**: Eski scaffold (15-may) wiped, Step 2'da clean re-init

## Step 2: ✅ Next.js 16 init (App Router) [DONE 2026-05-16]

- **Goal**: Clean Next.js 16 scaffold with TS + Tailwind v4 + Turbopack + App Router
- **Command**: `npx create-next-app@latest packages/frontend-web --typescript --tailwind --app --src-dir --no-eslint --import-alias "@/*" --use-npm --turbopack`
- **Acceptance**: `npm run dev` → http://localhost:3000 → HTTP 200, Ready in 514ms
- **Result**:
  - ✅ Next.js 16.2.6
  - ✅ React 19.2.4
  - ✅ Tailwind CSS v4
  - ✅ Turbopack enabled
  - ✅ TypeScript 5
  - ✅ Package name: `@yuzdanyuz/frontend-web`
  - ⚠️ AGENTS.md flagged: Next 16 has breaking changes — consult `node_modules/next/dist/docs/` before code
- **Effort**: 1 soat

## Step 3: ✅ TypeScript strict mode [DONE 2026-05-16]

- **Goal**: `tsconfig.json`'da `strict: true`, `noUncheckedIndexedAccess: true`, `noImplicitAny: true`
- **Files**: `tsconfig.json`
- **Acceptance**: `npx tsc --noEmit` → 0 errors
- **Result**: ✅ Added: `noUncheckedIndexedAccess`, `noImplicitOverride`,
  `noFallthroughCasesInSwitch`, `forceConsistentCasingInFileNames` (over Next.js default `strict: true`)
- **tsc check**: 0 errors
- **Effort**: 5 min

## Step 4: ✅ ESLint + Prettier + import sort [DONE 2026-05-16]

- **Goal**: Code style standards (`eslint-config-next` + prettier + sort-imports)
- **Files**: `eslint.config.mjs`, `.prettierrc.json`, `.prettierignore`, `package.json`
- **Acceptance**: `npm run lint` → 0 errors, `npm run format` formats
- **Result**:
  - ✅ ESLint 9.39 flat config (native — no FlatCompat needed for Next 16)
  - ✅ Imports: `eslint-config-next`, `/core-web-vitals`, `/typescript`, `eslint-config-prettier`
  - ✅ Prettier 3.8 + `@trivago/prettier-plugin-sort-imports` (custom order: react/next → 3rd party → @/ → relative)
  - ✅ Single quotes, semis, trailing commas, 100-char width
  - ✅ Scripts: `lint`, `lint:fix`, `format`, `format:check`
  - ⚠️ Next 16: `next lint` removed (codemod available)
- **Effort**: 30 min

## Step 5: ✅ Pre-commit hook [DONE 2026-05-16]

- **Goal**: Har commit'da auto-format + lint
- **Files**: `.pre-commit-config.yaml` (monorepo root, unified with backend)
- **Acceptance**: Test commit → lint runs automatically
- **Result**:
  - ✅ Python `pre-commit` framework (allaqachon backend uchun bor edi)
  - ✅ Yangi 2 hook: `frontend-prettier`, `frontend-eslint` (local, npx)
  - ✅ Files filter: `packages/frontend-web/.*\.(ts|tsx|js|jsx|...)$`
  - ✅ `pass_filenames: true` — faqat o'zgargan fayllar'ga run qiladi (tezroq)
  - ✅ Backend (ruff) + frontend (prettier+eslint) bir xil unified hook system
- **Pattern decision**: husky+lint-staged emas — Python pre-commit framework
  (monorepo'da allaqachon bor, backend bilan unified, oson maintenance)
- **Effort**: 20 min

## Step 6: ✅ Folder structure standard [DONE 2026-05-16]

- **Goal**: `src/` ichida discipline'd folders
- **Result**: 11 ta papka yaratildi (`.gitkeep` placeholder bilan):
  - `app/`, `components/{ui,domain,layouts}/`, `lib/`, `api/`, `hooks/`,
    `stores/`, `types/`, `i18n/`, `styles/`
- **Effort**: 5 min

## Step 7: ✅ Tailwind config from Claude Design tokens [DONE 2026-05-16]

- **Goal**: `~/.claude/skills/yuzdanyuz-design/colors_and_type.css` → Tailwind v4 config
- **Files**: `src/app/globals.css` (Tailwind v4 is CSS-first, no `tailwind.config.ts`)
- **Action**: `yuzdanyuz-design` skill invoke qilindi — full port
- **Acceptance**: `bg-primary` → indigo-500, `text-fg-2` → slate-600, dark mode parity
- **Result**:
  - ✅ `@import 'tailwindcss';` + `@custom-variant dark`
  - ✅ Full brand ramp (yy-indigo, yy-amber, yy-slate, semantic, league colors)
  - ✅ shadcn semantic aliases (--background, --primary, --card, --ring, etc.)
  - ✅ Light + dark mode parity via `.dark` / `[data-theme="dark"]`
  - ✅ Typography classes preserved (.t-h1...t-h6, .t-body, .t-caption, .t-overline)
  - ✅ Spacing/radii/shadows/motion tokens
  - ✅ Universal focus ring
  - ✅ Inter + JetBrains Mono Google Fonts
- **Verify**: dev server → CSS includes `yy-indigo-500`, `--primary:`, `.dark`, `t-h1`
- **Note**: Tailwind v4 uses `@theme inline` (CSS-first) — `tailwind.config.ts` not needed
- **Effort**: 30 min

## Step 8: ✅ shadcn/ui init + brand override [DONE 2026-05-16]

- **Goal**: `shadcn init` — components → tokens override
- **Files**: `components.json`, `src/components/ui/button.tsx`, `src/lib/utils.ts`, `src/app/globals.css`
- **Action**: `npx shadcn@latest init -t next -b radix -p nova -y --no-monorepo`
- **Acceptance**: `<Button variant="default">` brand indigo color
- **Result**:
  - ✅ Preset: nova (Lucide + Geist fonts)
  - ✅ Base: radix (Radix UI primitives)
  - ✅ Tailwind v4 detected by shadcn CLI
  - ✅ Dependencies: `class-variance-authority`, `clsx`, `tailwind-merge`, `radix-ui`, `lucide-react`, `tw-animate-css`
  - ✅ Brand override (oklch grayscale → yy-indigo/amber/slate):
    - `--primary` → `var(--yy-indigo-500)`
    - `--accent` → `var(--yy-amber-500)`
    - `--ring` → `var(--yy-indigo-500)`
    - `--sidebar-*` → brand-aligned (B2B admin shell)
    - `--chart-1..5` → indigo / amber / success / info / error palette
  - ✅ Dark mode: `--primary` lifts to indigo-400 (legibility)
  - ✅ Test page renders: 5 button variants + 5 color swatches + .t-num
  - ✅ TS check: 0 errors
- **Effort**: 1 soat

## Step 9: ✅ Primary components install (shadcn batch) [DONE 2026-05-16]

- **Goal**: 15 ta asosiy komponent install
- **Action**: `npx shadcn@latest add input card dialog dropdown-menu select tabs sonner badge avatar alert progress skeleton tooltip sheet -y`
- **Acceptance**: Har bittasi `src/components/ui/` ichida
- **Result**: 15/15 — alert, avatar, badge, button, card, dialog, dropdown-menu, input,
  progress, select, sheet, skeleton, sonner (toast), tabs, tooltip
- **Note**: `toast` → `sonner` (modern replacement)
- **Reminder**: Tooltip uchun root layout'da `<TooltipProvider>` o'rab qo'yish kerak (kelajak Step 26)
- **Effort**: 10 min

## Step 10: ✅ Environment config [DONE 2026-05-16]

- **Goal**: `.env.local` + `.env.example` (zero secrets in repo)
- **Files**: `.env.local` (gitignored), `.env.example` (tracked), `.gitignore` updated
- **Vars**: `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_SSE_BASE_URL`,
  `NEXT_PUBLIC_DEFAULT_LOCALE`, `NEXT_PUBLIC_SENTRY_DSN` (empty),
  `NEXT_PUBLIC_APP_VERSION`, `NEXT_PUBLIC_COMMIT_SHA`,
  `NEXT_PUBLIC_FEATURE_AI_TUTOR`, `NEXT_PUBLIC_FEATURE_LEADERBOARD_LIVE`
- **Verified**: `git check-ignore` confirms `.env.local` ignored, `.env.example` tracked
- **Effort**: 5 min

## Step 11: ✅ OpenAPI types generation [DONE 2026-05-16]

- **Goal**: `openapi-typescript` setup — backend schema → TS types
- **Files**: `package.json` script `generate-api`, `src/api/schema.ts` (auto-generated)
- **Command**: `openapi-typescript https://hsm.sammu.uz/yuzdanyuz/api/schema/ -o src/api/schema.ts`
- **Result**:
  - ✅ openapi-typescript 7.13.0 installed
  - ✅ `src/api/schema.ts` generated — 3794 satr
  - ✅ 100+ API path typed (auth, exams, leaderboard, intelligence, commerce, analytics, ...)
  - ✅ `npm run generate-api` script qo'shildi
  - ✅ TS check: 0 errors
- **Workflow**: backend deploy paytida `npm run generate-api` qaytadan ishlatish kerak (yangi endpoint'lar)
- **Effort**: 15 min

## Step 12: ✅ API client wrapper (fetch + auth) [DONE 2026-05-16]

- **Goal**: `src/api/client.ts` — typed fetch with JWT cookie included
- **Files**: `src/api/client.ts`, `src/api/error.ts`
- **Pattern**: `credentials: 'include'`, typed return via `paths` from schema
- **Result**:
  - ✅ `api<T>(path, init)` — low-level typed fetch
  - ✅ `apiGet<P>(path)` — path autocomplete, response type inferred
  - ✅ `apiPost<P>(path, body)` — path autocomplete, body + response typed
  - ✅ `APIError` class — `status`, `code`, `fieldErrors`, `isRateLimit`, `isUnauthorized`
  - ✅ Content-Type aware (JSON + plain text)
  - ✅ Real backend smoke verified:
    - `/health/` → 200 `{"status": "ok"}` ✅
    - `/ping/` → 200 `"pong"` (plain text) ✅
  - ✅ Cookie auth: `credentials: 'include'` — JWT auto-included
- **Bug fixed**: plain text response (e.g. `/ping/`) was JSON-parsed → SyntaxError; now content-type checked
- **Effort**: 45 min

## Step 13: ✅ TanStack Query setup [DONE 2026-05-16]

- **Goal**: `QueryClientProvider` in root layout, default options
- **Files**: `src/app/providers.tsx`, `src/app/layout.tsx`
- **Acceptance**: `useQuery` hook works in any client component
- **Result**:
  - ✅ TanStack Query v5.100 + DevTools (dev only)
  - ✅ `<Providers>` client component — QueryClient as `useState` (not module-level singleton — RSC-safe)
  - ✅ Default options:
    - `staleTime: 30s`, `gcTime: 5min`
    - `retry: 2` (skip 4xx — user/auth errors don't benefit from retry)
    - `refetchOnWindowFocus: false`
    - mutations: no retry
  - ✅ Root layout: lang="uz-Latn", brand metadata
  - ✅ Smoke test: home page `useQuery({queryFn: api('/health/')})` → renders backend status
- **Effort**: 20 min

## Step 14: ✅ Error envelope handler [DONE 2026-05-16]

- **Goal**: Backend `{success: false, error: {code, message}}` → toast + form errors
- **Files**: `src/api/error.ts`, `src/lib/toast.ts`, `src/app/providers.tsx`
- **Acceptance**: API error → user sees friendly toast (uz)
- **Result**:
  - ✅ `toast.fromApiError(err)` — APIError → friendly Uzbek toast
    - 401 → "Tizimga qayta kiring"
    - 429 → "Juda ko'p so'rov" + retry_after
    - 5xx → "Server xatosi"
    - Other → backend message + code
  - ✅ `toast.toFormErrors(err, setError)` — fieldErrors → react-hook-form
  - ✅ `<Toaster>` (sonner) — top-right, richColors, brand border
  - ✅ Global `MutationCache.onError` — toast for unhandled mutation errors
  - ✅ Per-mutation `onError` overrides global (caller takes responsibility)
  - ✅ TS + lint: 0 errors
- **Effort**: 30 min

## Step 15: ✅ i18n setup (next-intl) [DONE 2026-05-16]

- **Goal**: Uz-Latn primary, ru + en + Uz-Cyrl secondary
- **Files**: `src/i18n/{config,request}.ts`, `messages/{uz-Latn,uz-Cyrl,ru,en}.json`,
  `next.config.ts`, `src/app/layout.tsx`
- **Acceptance**: `t('home.greeting')` works, switching locale works
- **Result**:
  - ✅ next-intl 4.12 (Next 16 compatible per peerDependencies)
  - ✅ 4 ta locale to'liq: uz-Latn (default), uz-Cyrl, ru, en (~30+ strings each)
  - ✅ Translation keys: common, auth, nav, home, exam, result, error
  - ✅ `createNextIntlPlugin` wired in `next.config.ts`
  - ✅ Root layout: `NextIntlClientProvider` + `getLocale` + `getMessages`
  - ✅ Smoke test: home page renders 6 Uzbek strings correctly
    (YuzDanYuz, Yuklanmoqda, Kirish, Saqlash, Orqaga, Bekor qilish)
- **Note**: Locale switching UI (Step 59) keladi keyinroq — hozir env-var default
- **Effort**: 1 soat

---

# PHASE 1 — Auth flow (1 hafta)

> **Definition of Done**: Real backend bilan E2E login, JWT cookie set, protected route redirect ishlaydi.

## Step 16: 🔴 Login page UI

- **Goal**: `/login` page using `yuzdanyuz-design` Auth screen reference
- **Files**: `src/app/(auth)/login/page.tsx`, `src/components/forms/LoginForm.tsx`
- **Acceptance**: Form renders, validation ishlaydi (zod + react-hook-form)
- **Effort**: 3 soat

## Step 17: 🔴 OTP send + verify pages

- **Goal**: 2-step phone OTP flow
- **Files**: `src/app/(auth)/otp/send/page.tsx`, `src/app/(auth)/otp/verify/page.tsx`
- **Effort**: 3 soat

## Step 18: 🔴 Signup flow

- **Goal**: Phone + name + region → OTP → done
- **Files**: `src/app/(auth)/signup/page.tsx`
- **Effort**: 2 soat

## Step 19: 🔴 JWT cookie integration

- **Goal**: Backend `Set-Cookie: access_token=...; Path=/yuzdanyuz/; Secure` — browser auto-stores
- **Acceptance**: Login → response cookie → next request auto-includes
- **Effort**: 30 min

## Step 20: 🔴 `useMe()` hook

- **Goal**: `src/hooks/useMe.ts` — current user query
- **Pattern**: `useQuery({queryKey: ['me'], queryFn: () => api('/api/auth/me/')})`
- **Effort**: 30 min

## Step 21: 🔴 Protected route wrapper (`<RequireAuth>`)

- **Goal**: HOC/component that checks auth, redirects to /login
- **Files**: `src/components/auth/RequireAuth.tsx`, `src/middleware.ts`
- **Acceptance**: `/dashboard` access without auth → `/login?next=/dashboard`
- **Effort**: 1.5 soat

## Step 22: 🔴 Logout flow

- **Goal**: POST `/api/auth/logout/` → cookie clear → redirect home
- **Effort**: 30 min

## Step 23: 🟡 Password reset

- **Files**: `src/app/(auth)/password/reset/page.tsx`
- **Effort**: 2 soat

## Step 24: 🟡 Org switcher (multi-org user)

- **Files**: `src/components/auth/OrgSwitcher.tsx`
- **Acceptance**: Switch → JWT yangilanadi (current_org claim) → page reload
- **Effort**: 2 soat

## Step 25: 🔴 Auth E2E test

- **Goal**: Playwright test login → dashboard → logout
- **Files**: `e2e/auth.spec.ts`
- **Effort**: 1 soat

---

# PHASE 2 — Layout shell (1 hafta)

> **Definition of Done**: Har logged-in user shell ko'radi, route'lar shell ichida render.

## Step 26: 🔴 Student app layout — `src/app/(student)/layout.tsx`

## Step 27: 🔴 Top nav bar (responsive) — `src/components/layouts/TopNav.tsx`

## Step 28: 🔴 Bottom tab bar (mobile only) — 4 tab (Home/Imtihonlar/Reyting/Profil)

## Step 29: 🔴 Sidebar (desktop only) — collapsible 240px → 64px

## Step 30: 🟡 Footer — minimal (copyright, lang switcher, support)

## Step 31: 🔴 Toast system (sonner)

## Step 32: 🔴 Modal/Dialog system (shadcn dialog)

## Step 33: 🔴 Skeleton + loading states + Suspense boundaries

## Step 34: 🔴 Empty states — reusable `<EmptyState>` (Claude Design SVGs)

## Step 35: 🔴 Error boundary + 404 + 500 pages

---

# PHASE 3 — Student core (3 hafta — MVP critical)

> **Definition of Done**: Real student real imtihonni real backend bilan topshira oladi.

## Step 36: 🔴 Home dashboard (next exam, streak, league, quick actions)

## Step 37: 🔴 Exam list page (tabs + filter + status badges)

## Step 38: 🔴 Exam detail / instructions page

## Step 39: 🔴 Question renderer — Single Choice (SC)

## Step 40: 🔴 Question renderer — Multiple Choice (MC)

## Step 41: 🟡 Question renderer — Matching (MT) — drag-drop

## Step 42: 🟡 Question renderer — Ordering (OR) — sortable

## Step 43: 🟡 Question renderer — Fill Blanks (FB) — inline text inputs

## Step 44: 🟡 Question renderer — Open-Ended (OE) — textarea + autosave

## Step 45: 🟢 Question renderer — File Upload (FU) — drag-drop file

## Step 46: 🔴 Timer + heartbeat (POST polling) — `useHeartbeat` hook

## Step 47: 🔴 Anti-cheat banner + warning modal

## Step 48: 🔴 Submit confirmation modal

## Step 49: 🔴 Results page (ScoreGauge + breakdown + AI tahlil)

## Step 50: 🟡 Practice mode (topic picker → custom session)

---

# PHASE 4 — Engagement (2 hafta)

## Step 51: 🔴 Leaderboard global (top 50 + my rank)

## Step 52: 🟡 Leaderboard region/tenant tabs

## Step 53: 🔴 Streak detail + calendar heatmap (365 day)

## Step 54: 🟡 League standings page

## Step 55: 🟢 League promote/demote animation (Lottie/Framer Motion)

## Step 56: 🔴 Notification feed page (list + mark-as-read)

## Step 57: 🟡 PWA push notifications (Web Push + service worker)

## Step 58: 🔴 Profile page (identity, language, telegram link, achievements)

## Step 59: 🔴 Settings page (account, privacy, GDPR, language, theme)

## Step 60: 🟢 Achievement badges grid

---

# PHASE 5 — Commerce (2 hafta)

## Step 61: 🔴 Wallet history page (balance + tx list)

## Step 62: 🔴 Subscription plans page (3-tier comparison)

## Step 63: 🔴 Checkout flow — Payme (redirect + callback)

## Step 64: 🔴 Checkout flow — Click (same pattern)

## Step 65: 🔴 Payment status polling page

## Step 66: 🟢 Refund request flow

## Step 67: 🟡 Affiliate dashboard (code + earnings + cashout)

## Step 68: 🟡 Affiliate code share UI (Telegram/WhatsApp/copy)

## Step 69: 🟢 Coin earn/spend log (granular)

## Step 70: 🔴 Commerce E2E test (signup → buy plan → wallet credit)

---

# PHASE 6 — Intelligence (2 hafta)

> ⚠️ **Backend prerequisite**: SSE migration (per `packages/backend/docs/websocket_strategy.md`)

## Step 71: 🔴 AI tutor chat UI (SSE streaming via `EventSource`)

## Step 72: 🔴 Skill profile radar chart (Recharts)

## Step 73: 🟡 Open-ended submission editor (textarea + word count)

## Step 74: 🟡 Open-ended feedback view (rubric breakdown)

## Step 75: 🟡 Question recommendation widget (dashboard carousel)

## Step 76: 🟢 Study plan view (weekly AI-generated)

## Step 77: 🟢 Strengths/weaknesses dashboard

## Step 78: 🔴 Intelligence E2E test (essay → AI feedback → skill update)

---

# PHASE 7 — B2B admin (3 hafta)

## Step 79: 🔴 B2B layout (sidebar + dense tables, Linear-style)

## Step 80: 🔴 Org dashboard (KPIs + recent activity)

## Step 81: 🔴 Member CRUD (data table — filter/add/edit/suspend)

## Step 82: 🟡 Bulk import wizard (CSV drag-drop → preview → import job)

- **Blocker**: ISSUE-406 backend bulk API

## Step 83: 🔴 Subscription management (current plan + upgrade + billing)

## Step 84: 🔴 Analytics dashboard (subject avg, growth, distribution, weak)

## Step 85: 🔴 Report export wizard (filter + format → async job → download)

## Step 86: 🟡 Webhook endpoints UI (create + list + delivery log)

## Step 87: 🟢 Audit log viewer (django-simple-history rows)

## Step 88: 🟡 Per-tenant settings page (JSON schema editor)

---

# PHASE 8 — Marketing site (1 hafta)

## Step 89: 🔴 Landing page (3-audience hero + features + testimonials + CTA)

## Step 90: 🔴 Pricing page (3-plan toggle + comparison table)

## Step 91: 🟡 About + contact

## Step 92: 🟢 Blog (MDX — DTM tips, success stories)

---

# PHASE 9 — Mobile (React Native, 3 hafta)

## Step 93: 🔴 Expo init + shared types (`packages/frontend-mobile/`)

## Step 94: 🔴 Student core flow on RN (login → dashboard → exam → result)

## Step 95: 🔴 Push notifications (Expo Notifications)

## Step 96: 🔴 App store submission prep (icons, splash, screenshots, EAS Build)

---

# PHASE 10 — Polish & launch (2 hafta)

## Step 97: 🔴 Accessibility audit (WCAG 2.1 AA, axe-core in CI)

## Step 98: 🔴 Performance optimization (Lighthouse 90+ mobile)

## Step 99: 🔴 Multi-language QA (uz-Latn, uz-Cyrl, ru, en native speakers)

## Step 100: 🔴 Production deploy + soft launch checklist

- Vercel production + custom domain
- Sentry frontend
- Cloudflare CDN
- 50-100 beta user invite (Telegram channel)
- Daily monitoring (Sentry, Vercel analytics, user feedback)

---

# 🎯 Decision points

| DP  | After Step | Choice                                                  | Recommendation                   |
| --- | ---------- | ------------------------------------------------------- | -------------------------------- |
| DP1 | 15         | Boshlash route — `(auth)` yoki `(student)` placeholder? | `(auth)` first                   |
| DP2 | 25         | Shell birinchi yoki dashboard birinchi?                 | Shell first (consistency)        |
| DP3 | 50         | Engagement yoki Commerce keyin?                         | Engagement (retention loop)      |
| DP4 | 70         | **SOFT LAUNCH** point — 50-100 beta?                    | Yes if pre-launch checklist done |
| DP5 | 88         | Mobile yoki Marketing keyin?                            | Marketing first (1h vs 3h)       |
| DP6 | 96         | Public launch hozir yoki polish?                        | Polish shart                     |

---

# ⚠️ Risk register

| Risk                     | Phase | Mitigation                                              |
| ------------------------ | ----- | ------------------------------------------------------- |
| Backend SSE not ready    | 6     | Implement before Step 71 (`docs/websocket_strategy.md`) |
| Bulk API not done        | 7     | Step 82 dependent; Phase 7 ships without it             |
| AI tutor cost spike      | 6     | cache_control + rate limit (5/min/user)                 |
| Mobile app rejected      | 9     | Compliance review pre-submission                        |
| i18n missing             | 10    | Step 99 native speaker review                           |
| Payme/Click breaks       | 5     | Stub dev, prod creds final test                         |
| Production crash returns | All   | Monitor + old playbook                                  |

---

# 📅 Realistic timeline (single dev + Claude Code)

```
Hafta 1-2   Phase 0   Foundation
Hafta 3     Phase 1   Auth
Hafta 4     Phase 2   Shell
Hafta 5-7   Phase 3   Student core  ← DP4: SOFT LAUNCH POSSIBLE
Hafta 8-9   Phase 4   Engagement
Hafta 10-11 Phase 5   Commerce
Hafta 12-13 Phase 6   Intelligence
Hafta 14-16 Phase 7   B2B admin
Hafta 17    Phase 8   Marketing
Hafta 18-20 Phase 9   Mobile RN
Hafta 21-22 Phase 10  Polish + public launch  ← PUBLIC LAUNCH ~2027-Q1
```

**MVP soft launch**: 6-8 hafta
**Feature-complete web**: 17 hafta (~4 oy)
**Mobile + polish**: +5 hafta (~5.5 oy total)

---

# 🔧 Tooling stack

| Layer          | Choice                            | Why                               |
| -------------- | --------------------------------- | --------------------------------- |
| Framework      | Next.js 16 App Router + Turbopack | Latest, edge-ready, Vercel-native |
| Language       | TypeScript strict                 | Type safety, refactor confidence  |
| UI library     | shadcn/ui                         | Copy-source, Tailwind-native      |
| Styling        | Tailwind CSS v4                   | Design tokens from Claude Design  |
| State (server) | TanStack Query v5                 | Cache, optimistic, retries        |
| State (client) | Zustand                           | Lightweight, no boilerplate       |
| Forms          | react-hook-form + zod             | Type-safe validation              |
| i18n           | next-intl                         | App Router native, RSC-aware      |
| E2E test       | Playwright                        | Cross-browser, MS official        |
| Unit test      | Vitest                            | Faster than Jest, ESM-native      |
| Charts         | Recharts / Tremor                 | React-native, Tailwind-friendly   |
| Animation      | Framer Motion                     | Promote/demote, transitions       |
| Icons          | Lucide                            | Per design system rules           |
| Storybook alt  | Ladle                             | Faster than Storybook             |
| Deploy         | Vercel                            | Free tier, preview branches       |
| Mobile         | Expo (bare workflow)              | EAS Build, OTA updates            |
| Push           | Expo Notifications + Web Push     | Cross-platform                    |

---

# ✅ Per-phase "Done" formula

Bir faza tugadi:

1. ✅ Har step acceptance criteria bajarilgan
2. ✅ E2E test yashil
3. ✅ Lighthouse score baseline
4. ✅ Mobile responsive (Chrome DevTools 3G)
5. ✅ i18n complete (uz-Latn ALL strings)
6. ✅ Code review (Claude code-reviewer agent)
7. ✅ No console errors / warnings
8. ✅ TypeScript no errors
9. ✅ Git commit + push (conventional)

---

# 🚀 Launch criteria

**Pre-launch shart**:

- [ ] Pre-launch BLOCKER checklist (`packages/backend/docs/pre_launch_checklist.md`) DONE
- [ ] Soft launch 4 hafta — 0 critical bug
- [ ] 50+ beta feedback addressed
- [ ] Load test 500 concurrent users
- [ ] Sentry frontend + backend
- [ ] Status page live
- [ ] DR runbook tested
- [ ] Payment provider production creds verified
- [ ] Telegram bot ready
- [ ] Email SPF/DKIM/DMARC verified
- [ ] App store approved (mobile)
- [ ] Marketing site SEO complete

---

# 📞 Bu hujjat'dan qanday foydalanish

1. **Review qiling** — har step ma'qulmi, prioritet to'g'rimi
2. **Feedback** — qo'shimcha step, prioritet o'zgartirish
3. **Tasdiqlangandan keyin** — Step 1 dan boshlaymiz
4. **Har faza oxirida** — review session

---

**Last updated**: 2026-05-16 (Phase 0 — Foundation 15/15 DONE)
**Version**: v1.2
**Status**: 15/100 done — Phase 1 (Auth flow) Step 16 next
