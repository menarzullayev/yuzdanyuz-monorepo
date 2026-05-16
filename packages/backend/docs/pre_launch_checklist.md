# Pre-Launch Backend Checklist

> **Maqsad**: Real customer launch'dan oldin hal qilinishi kerak bo'lgan ishlar
> **Yaratilgan**: 2026-05-16 (bugungi production stabilization sessiyasidan keyin)
> **Holat**: Backend production'da TIRIK (`https://hsm.sammu.uz/yuzdanyuz/`), launch'gacha 4 ta blocker + 8 ta high-priority

---

## 📊 Tezkor xulosa

| Kategoriya | Soni | Real launch'gacha |
|---|---|---|
| 🔴 **BLOCKER** — launch'dan oldin shart | 2 | Hammasi |
| 🟠 **HIGH** — launch'gacha qilinsin | 8 | Eng kamida 6 |
| 🟡 **MEDIUM** — birinchi oy ichida | 12 | — |
| 🟢 **FUTURE** — kelajakda | 10+ | — |
| ⚪ **REAL PRODUCTION OLDIDAN** — final checklist | 3 | Manual, esda bo'lsin |

**Bugungi achievement**: 4/5 muammo yechildi, migrations applied. Backend hozir **production-LIVE** subpath'da. Qolgan ish — operational safety net'lar va frontend.

---

## 🔴 BLOCKER — Launch'dan oldin shart

### 1. WebSocket → SSE migration (Apache mod_proxy_wstunnel'siz)
- **Constraint**: Subdomain yo'q, admin yo'q, PHP cURL WebSocket qilmaydi
- **Yangi yo'l — Server-Sent Events (SSE)**:
  - SSE oddiy HTTP/1.1 streaming response — `Content-Type: text/event-stream`
  - PHP cURL **SSE'ni stream qila oladi** (`CURLOPT_WRITEFUNCTION` + `flush()` per chunk)
  - Bizning use case'lar SSE'ga to'liq mos keladi (server→client one-way):
    - ✅ Anti-cheat warnings (server → "strike!" pushes to client)
    - ✅ Leaderboard real-time updates (server → rank changes)
    - ✅ AI tutor streaming (server → token-by-token)
    - ✅ Notification feed (server → "new notification")
  - Client → server: oddiy POST (heartbeat = `POST /heartbeat/` har N sek)
- **Action**:
  - Backend: `apps/exams/consumers.py` (Channels WebSocket) → `apps/exams/sse_views.py` (Django StreamingHttpResponse) ga rewrite
  - `django-eventstream` paket — async SSE helper
  - `index.php` (PHP proxy) ga SSE streaming support qo'shish (output buffering off + curl chunked transfer)
  - Frontend: `EventSource API` ishlatish (`new EventSource('/yuzdanyuz/api/v1/exams/{id}/events/')`)
- **Effort**: 1-2 kun (backend SSE views + PHP streaming + frontend EventSource client)
- **Document**: `docs/websocket_strategy.md` (yangi) — SSE pattern + why we chose this

### 2. ~~Load test (smoke)~~ ✅ DONE 2026-05-16

**Natija**: Backend code sog'lom (p50 < 20ms, p95 < 400ms hatto 200u stress'da).
**PHP proxy overhead**: 8-10ms konstant (kutilgan 15ms'dan kamroq).
**Bottleneck**: Apache MPM ~82 RPS ceiling (MVP uchun yetarli, post-launch tuning).
**Memory leak**: yo'q.
**To'liq report**: `/home/hsm/load-test/2026-05-16/`

### 2b. Rate limiter redesign (load test'dan topilgan) — YANGI BLOCKER
- **Risk**: Hozirgi `ip:<ip>` 30 req/min/IP — shared NAT (maktab, office, mobile carrier) ostida REAL user'lar blocklanadi
- **Action**:
  - Authenticated: `user:<user_id>` bucket (per-user, NAT immune)
  - Endpoint-specific limits:
    - `/accounts/login/`: 5/min (brute force protection — qo'shilgan)
    - `/api/v1/*` (browse): 120/min (oddiy navigation)
    - `/admin/*`: 60/min
    - `/api/v1/exams/*/anticheat/`: 30/min (anti-cheat spam guard)
  - Org-level (B2B): `org:<org_id>` aggregate — tenant uchun fair share
  - Backoff progressive: 1/5/30/120 min (hozir 1/5/60/1440 — 24 soat juda agressiv)
- **Effort**: 30 daqiqa kod + 15 daqiqa test
- **Owner**: Backend dev (men)
- **Priority**: 🔴 BLOCKER — real maktab user'lar blocked bo'ladi

---

## 🟠 HIGH PRIORITY — Launch'gacha qilinsin

### 7. Frontend (ISSUE-F01)
- **Status**: Design system v1 tayyor, skill registered, hech narsa qurilmagan
- **Effort**: 12-16 hafta MVP (boshlanyapti)

### 8. Monitoring + alerting
- **Status**: Prometheus metrics endpoint mavjud (`/metrics`), lekin scrape qilinmayapti
- **Action**:
  - Grafana Cloud free tier (10K series, 14d retention)
  - Scrape config qo'shish (Grafana Agent in jail OR push pattern)
  - Critical alerts:
    - HTTP 5xx rate > 1% / 5min
    - p95 latency > 3s / 5min
    - Celery DLQ growth > 0 / hr (yangi failed task)
    - DB connection count > 80% (saturation)
    - Disk usage > 90%
    - Redis memory > 90%
- **Effort**: 1 kun

### 9. Retention cron tasks
- **Status**: `django-simple-history` jadvallar o'sadi (har CRUD bir qator). Yetishmagan task:
  - `clean_old_history` — har model uchun retention policy
  - `clean_failed_tasks` — DLQ > 30 kun eski'larini o'chirish
  - `clearsessions` — Django built-in, lekin schedule qilinmagan
  - `clean_old_leaderboard_snapshots` — > 2 yil eski snapshot'lar
- **Action**: Celery beat schedule'ga qo'shish (har kunda 03:00'da)
- **Effort**: 4 soat

### 10. Email deliverability (SPF/DKIM/DMARC)
- **Risk**: Hozir email yuborilmasligi mumkin (Gmail/Yandex spam'ga tushishi mumkin)
- **Action**:
  - DNS records (siz domain owner siz):
    - SPF: `v=spf1 include:_spf.google.com ~all` (yoki SMTP provider'iga ko'ra)
    - DKIM: provider'dan key olib, DNS'ga qo'shish
    - DMARC: `v=DMARC1; p=quarantine; rua=mailto:dmarc@hsm.sammu.uz`
  - Test: mail-tester.com
- **Effort**: 30 daqiqa DNS + 1 soat test

### 11. SMS balance + auto-topup alert
- **Risk**: PlayMobile balance tugasa, OTP yuborilmaydi → user signup ishlamaydi
- **Action**:
  - Celery beat task: har kuni balance check via PlayMobile API
  - Threshold (e.g. < 100K UZS) → Telegram alert + email
- **Effort**: 2 soat

### 12. Status page setup
- **Risk**: Server tushganda user'lar qachon tiklanishini bilmaydi
- **Variantlar**:
  - statuspage.io ($29/oy)
  - upstash status (free)
  - O'zingiz qurish (`status.hsm.sammu.uz` static HTML + cron health check)
- **Effort**: 2 soat (eng oddiy variant)

### 13. Disaster Recovery runbook
- **Status**: yo'q — yangi yozish kerak
- **Content**:
  - Full DB restore from backup (step-by-step)
  - Application restart sequence (postgres → redis → web → asgi → celery)
  - PHP proxy emergency disable (.htaccess'ni rename)
  - "Server hech narsa qaytarmayapti" diagnostika 5 daqiqada
  - Contact list (siz, admin, agar bor bo'lsa)
- **Location**: `docs/DR_PLAN.md`
- **Effort**: 3 soat

### 14. ISSUE-402 GDPR erasure user-facing flow
- **Status**: Backend stub bor (`/api/auth/gdpr/erasure/`), frontend UI yo'q
- **EU launch uchun shart**. Uzbekiston launch'da emas.
- **Effort**: 1-2 hafta frontend + backend integration

---

## 🟡 MEDIUM — Birinchi oy ichida

### 15. WebSocket on subdomain
- Long-polling MVP ishlaydi, lekin subdomain bilan native WS UX yaxshilanadi
- Sabab: anti-cheat 5s latency emas, 100ms bo'lsa kuchli

### 16. ISSUE-401 PII redact celery task
- django-simple-history rows GDPR erasure paytida user PII redact kerak
- Hozir manual

### 17. ISSUE-403 SAML SSO
- B2B enterprise sotuvi uchun

### 18. ISSUE-406 Bulk API
- 10K student onboarding uchun

### 19. ISSUE-407 Long-lived API tokens
- Server-to-server integration

### 20. Performance baseline + SLO
- Aniq SLO'lar belgilash:
  - HTTP API p95 < 1s
  - Exam submit p95 < 2s
  - AI tutor p95 < 15s
  - Login flow < 3 step
- Grafana dashboard'da kuzatish

### 21. Code quality batch (deferred)
- ISSUE-202 views split (incremental — har sprint 1 app)
- ISSUE-206 serializer field naming (deprecate + rename 6 oy ichida)
- ISSUE-307 caching adoption (hot path'larga decorator qo'shish)

### 22. Watchdog v2
- Hozirgi watchdog primitive. Smart version:
  - Orphan gunicorn detection
  - Port conflict resolution
  - Graceful restart logic
- Hozirgi yetadi, lekin scale'da kerak

### 23. PHP keepalive security review
- `_yuzdanyuz_keepalive.php` (PHP-FPM uzun yashash mexanizmi)
- Audit: token rotation, IP whitelist, audit log
- O'rta priority, lekin "rootsiz" arxitektura'ning yagona qonuniy lyuki

### 24. SECRET_KEY auto-rotation policy
- Har 90 kunda yangilash policy
- Rotation paytida JWT cookie'lar invalid (user re-login)
- Schedule + runbook

### 25. Old `_yuzdanyuz_cleanup.php` pattern security review
- Hozir o'chirilgan, lekin agar kelajakda yana yozsak xavfsizlikni ta'minlash
- Token-based, IP whitelist, audit log, post-use delete

### 26. Pen test (eksternal)
- Pre-launch yoki post-launch first month
- Vendor: HackerOne bug bounty yoki local pen test firma
- Cost: $2K-$10K bir martalik

---

## 🟢 FUTURE — Kelajak yo'l xaritasi (issues.md'da to'liq)

### 27-36. Issues.md'dagi infra-blocked + frontend-blocked
- ISSUE-501 ClickHouse real integration
- ISSUE-502 CDC pipeline (Debezium/Kafka)
- ISSUE-503 Temporal/KEDA ScaledJob
- ISSUE-504 Multi-region active-passive
- ISSUE-506 SOC2 Type 1 audit
- ISSUE-F02 K8s deploy (Hetzner/DOKS)
- ISSUE-408 stevedore plugin migration
- ISSUE-X02 tox enforcement
- ISSUE-X06 advanced logging (Loki/CloudWatch)
- ISSUE-505 feature flag adoption (env booleans'ni flag'larga ko'chirish)

---

## ⚪ REAL PRODUCTION OLDIDAN (manual checklist — real user'lar uchun launch'dan oldin)

Bu 3 narsa **technical implementation emas, operational discipline**. User real launch'dan oldin shu fayl'ni qaytib ko'rib chiqishi va checked qilishi kerak.

### a. Secret rotation (final)
- **Nima**: Hozirgi `.env` ichidagi barcha sirlarni real launch oldidan rotate qilish
- **Nima uchun bugun emas**: Bu bir martalik ish, har deploy'da emas. Real customer kelishidan oldin 30 daqiqa.
- **Checklist (launch oldidan bajariladi)**:
  - [ ] Anthropic console → new API key → `.env`'da `ANTHROPIC_API_KEY` yangilash
  - [ ] @BotFather → revoke + new token → `.env`'da `TELEGRAM_BOT_TOKEN` yangilash + webhook URL yangilash
  - [ ] Django `python manage.py shell` → `from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())` → `.env`'da `SECRET_KEY` yangilash
  - [ ] PayMe / Click merchant credentials — real production credentials (hozir stub)
  - [ ] Database password — kelajak optimizatsiya (hozir socket trust auth)
  - [ ] **Restart all services** — `supervisorctl restart all`
  - [ ] Test: `/yuzdanyuz/health/` → 200, login flow → end-to-end
- **Effort**: 30 daqiqa (launch oldidan)

### b. Backup automation (final)
- **Nima**: pg_dump cron + off-site copy
- **Nima uchun bugun emas**: Hozir DB 30 KB (bo'sh), real data yo'q. Manual backup yetadi. Real user data paydo bo'lganda kerak.
- **Checklist (real customer signup'lar boshlangach)**:
  - [ ] `~/scripts/db-backup.sh` yozish (pg_dump + gzip + retention rotate)
  - [ ] Cron entry: `0 */6 * * * ~/scripts/db-backup.sh` (har 6 soat)
  - [ ] Off-site target tanlash: rsync VPS / Backblaze B2 / Telegram bot
  - [ ] Restore test (cheklov: hozircha dev DB'ga)
  - [ ] Monitor: backup fail bo'lsa Telegram alert
- **Effort**: 2 soat

### c. Sentry DSN actually set
- **Nima**: Production error tracking
- **Nima uchun bugun emas**: Hozir real traffic yo'q. Error'lar Django log'larida ko'rinadi. Sentry budget oqibati ham bor.
- **Checklist (real traffic boshlangach yoki bunchakta)**:
  - [ ] Sentry.io'da YuzDanYuz project create (free tier 5K events/oy)
  - [ ] `.env`'ga `SENTRY_DSN=https://...` qo'shish
  - [ ] `prod.py`'da Sentry SDK integration tasdiqlash (allaqachon kod bor)
  - [ ] Test: `manage.py shell` → `1/0` → Sentry dashboard'da event
  - [ ] Alert rules: > 10 error/min → email/Telegram
- **Cost**: $0/oy MVP, $26/oy paid plan agar 5K event yetmasa
- **Effort**: 30 daqiqa

---

## 🚨 Bugun emas, lekin esda bo'lsin (post-mortem'dan)

### Documentation gaps (DR + ops)
- ✅ `docs/issues.md` — issue tracker (mavjud)
- ❌ `docs/DR_PLAN.md` — disaster recovery (MUST WRITE)
- ❌ `docs/RUNBOOK.md` — common ops (restart, troubleshoot)
- ❌ `docs/websocket_strategy.md` — WebSocket decision
- ❌ `docs/jail_constraints.md` — HestiaCP jail what works/doesn't
- ✅ `docs/api_conventions.md` — API standards
- ✅ `docs/ARCHITECTURE.md` — architecture

### Operational tools missing
- ❌ Status page
- ❌ Grafana dashboards
- ❌ Alertmanager rules
- ❌ Backup verification (restore test cron)
- ❌ On-call escalation (none — solo dev)

### Single-dev burnout risk
- **Real concern**: Backend 800+ test, 42 issues, frontend just starting. Single dev shouldn't do all.
- **Action**: Hire 1 backend engineer + 1 frontend engineer when revenue allows
- **Or**: Open-source komponentlarni use qilish, kam yozish

---

## 📅 Suggested timeline

```
2026-05-17 (bugun + ertaga)
  ✅ Backend production fixes (done)
  → Frontend scaffold (Next.js init, design tokens port)

2026-05-18..2026-06-15 (4 hafta)
  → Frontend MVP (auth + student core flow)
  → Pre-launch BLOCKER fixes (secrets, backup, sentry, load test)

2026-06-16..2026-06-30 (2 hafta)
  → Soft launch (10-50 beta users)
  → HIGH priority items (monitoring, alerts, status page, retention cron)
  → Bug fix iterations

2026-07-01..onwards
  → Public launch
  → MEDIUM items as needed
  → FUTURE items per scale/revenue
```

---

## 📞 Bu hujjatdan qanday foydalanish

1. **Har hafta**: Yangi tugagan item'larni ✅ qiling, yangi topilganlarni qo'shing
2. **Pre-launch sprint planning**: 🔴 BLOCKER'lardan boshlang, har biriga effort + owner belgilang
3. **Quarterly review**: 🟢 FUTURE roadmap'ni biznes prioritetga moslang
4. **Yangi developer onboarding**: Bu fayl + `docs/issues.md` + `docs/ARCHITECTURE.md` — 3 fayl, butun context

---

**Last updated**: 2026-05-16 (production launch readiness baseline)
**Owner**: @narzullayevme
**Next review**: Frontend MVP'dan keyin
