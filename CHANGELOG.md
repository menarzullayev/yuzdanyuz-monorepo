# Changelog

Versions follow [Semantic Versioning](https://semver.org/); entries after 0.1.0 are written
by release-please from Conventional Commits (`.github/workflows/release-please.yml`).

## [0.2.0](https://github.com/menarzullayev/yuzdanyuz-monorepo/compare/v0.1.0...v0.2.0) (2026-11-27)


### Features

* **deploy:** single-host production compose stack behind Cloudflare Tunnel ([#100](https://github.com/menarzullayev/yuzdanyuz-monorepo/issues/100)) ([41fd933](https://github.com/menarzullayev/yuzdanyuz-monorepo/commit/41fd93343886258e88ef5feb23a04492f910564a))

## 0.1.0 (2026-11-26)

First tagged version: the state of `main` after the production-readiness audit work.

### Features

* Auth rewrite: login + password, Telegram, Google; optional TOTP 2FA, device sessions,
  Telegram and admin recovery; onboarding with exam goals.
* Exam engine: mock exams with a server-enforced time limit, official sittings with Rasch
  scaling and certificates, mistake notebook, question quality, content plan.
* Student web app (Next.js): exam list, exam runner, results, certificates; six locales
  including Karakalpak and the new Uzbek alphabet.
* AI: provider admin with encrypted keys, grading queue, AI/teacher calibration, Speaking.
* Business: tariffs, promo codes, B2B access keys, finance and funnel reports, monthly org
  report as CSV, HTML and PDF.
* Engagement: arena, ratings, typing practice, live monitoring; partnership CRM, groups,
  lessons; Telegram notices for certificates and partnership applications.
* Operations: status page, security events, audit log, bot admin, runtime kill switches.

### Security and reliability

* All P0 and P1 audit items closed (payments webhook, Telegram takeover, client IP,
  Helm/nginx deploy path, RLS on the runtime role, SSRF, data protection, observability).
* P2 items closed: error envelope, Tashkent day boundary, retry jitter, permission codes,
  Permissions-Policy, SMS circuit breaker, graceful shutdown, Sentry releases, digest-pinned
  images, API schema gate, E2E with axe, bundle budget, cost alerts, OTel collector, profiling.
