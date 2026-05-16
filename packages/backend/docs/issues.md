# YuzDanYuz — Architecture Issues & Hardening Roadmap

> **Manba**: [ARCHITECTURE_REVIEW_2026-05-16.md](ARCHITECTURE_REVIEW_2026-05-16.md)
> **Maqsad**: Senior architecture review topilmalarini trackable task'larga aylantirish
> **Yangilash siklı**: Har sprint oxirida status'larni yangilang, completed item'larni `✅`'ga o'tkazing
> **Hosting**: Bu fayl tracking uchun. Real ish — GitHub Issue → PR → squash merge.

---

## 📊 Status legendasi

| Belgi | Holat | Tushuntirish |
|---|---|---|
| ✅ | **Done** | To'liq bajarilgan, main'da, tasdiqlangan |
| 🟢 | **In Progress** | Hozir ustida ishlanmoqda (PR ochiq) |
| 🟡 | **Partial** | Qisman bajarilgan, qolgan ish bor |
| 🔵 | **Planned** | Reja'da bor, hali ishlanmagan |
| ⚪ | **Won't Fix** | Ataylab bajarmaymiz (sabab tushuntirilgan) |
| 🔴 | **Blocked** | Tashqi dependency tufayli to'xtagan |

## Severity legendasi

| Belgi | Daraja | Ta'rif |
|---|---|---|
| 🔴 | **P0** | Production bloker. Hozir tuzatilmasa scaling/security imkonsiz |
| 🟠 | **P1** | Yuqori. Keyingi sprint'da kerak, aks holda jamoa bloklangan |
| 🟡 | **P2** | O'rta. Bu quarter'da bajarilsin, keyingi feature uchun blocker |
| 🟢 | **P3** | Past. Kelajak optimization, hozir critical emas |

---

## 📈 Umumiy progress

| Sprint | Items | Done | Partial | Undone |
|---|---|---|---|---|
| Sprint 0 (E2E fixes) | 4 | ✅ 4 | 0 | 0 |
| Sprint 1 (Bleeding wounds) | 7 | ✅ 3 | 0 | 🔵 4 |
| Sprint 2-3 (Frontend unblock) | 6 | 0 | 0 | 🔵 6 |
| Q2 (Scalability) | 8 | 0 | 0 | 🔵 8 |
| Q3 (Enterprise readiness) | 9 | 0 | 0 | 🔵 9 |
| Q4 (Production-grade) | 6 | 0 | 0 | 🔵 6 |
| **JAMI** | **40** | **7** | **0** | **33** |

---

# Sprint 0 — Allaqachon bajarilgan (E2E sinov natijasi)

> Bu bo'lim hozirgi audit'gacha qilingan work-around va fix'lar. Reference uchun.

## ✅ ISSUE-000 — `ANTHROPIC_API_KEY` settings'ga export
- **Status**: ✅ **Done** (PR #39)
- **Severity**: 🟠 P1
- **Fayllar**: `core/settings/base.py:264-266`
- **Tavsif**: `.env`'da kalit bor edi, lekin `settings.ANTHROPIC_API_KEY` orqali o'qib bo'lmasdi. AI tutor task'lari fail bo'lardi.
- **Acceptance**: ✅ Settings export, real Anthropic call tasdiqlandi (728-char javob)
- **Lesson**: LESSONS.md → Lesson 16

## ✅ ISSUE-001 — `WhiteNoiseMiddleware` requirements'da to'g'ri joylash
- **Status**: ✅ **Done** (PR #40)
- **Severity**: 🟠 P1
- **Fayllar**: `requirements/base.txt`, `requirements/prod.txt`
- **Tavsif**: Middleware base.py'da edi, lekin paket faqat prod.txt'da. CI fail bo'ldi.
- **Acceptance**: ✅ Paket base.txt'ga ko'chirildi, CI green
- **Lesson**: LESSONS.md → Lesson 17

## ✅ ISSUE-002 — OpenEnded `question_version` type validation
- **Status**: ✅ **Done** (PR #41)
- **Severity**: 🟠 P1
- **Fayllar**: `apps/intelligence/views.py:117-157`
- **Tavsif**: SC savolga essay submit qilinsa, keraksiz Anthropic API chaqiriladi.
- **Acceptance**: ✅ View 400 qaytaradi agar `qv.question.type ∉ {OE, FU}`, negative test qo'shildi
- **Lesson**: LESSONS.md → Lesson 18

## ✅ ISSUE-003 — Self-hosted PHP-FPM watchdog (HestiaCP jail'ga moslashgan)
- **Status**: ✅ **Done** (production'da)
- **Severity**: 🔴 P0 (lifecycle uchun)
- **Fayllar**: `_yuzdanyuz_keepalive.php`, `private/yuzdanyuz/watchdog.sh`
- **Tavsif**: `jailbash --die-with-parent` muammosi. PHP-FPM jail orqali persistent watchdog.
- **Acceptance**: ✅ Failure recovery 60s ichida (30s detect + 30s startup)
- **Memory**: yuzdanyuz_persistent_backend.md

---

# Sprint 1 — Bleeding wounds (BU HAFTA)

> Production'ni 10K DAU'gacha xavfsiz olib chiqish uchun blocker'lar.
> **Kuch**: 1 senior engineer × 1 hafta

## ✅ ISSUE-101 — N+1 sweep across all views
- **Status**: ✅ **Done** (2026-05-16, direct commit)
- **Severity**: 🔴 P0
- **Effort**: 3-5 kun → real 2 soat (audit topgan hotspot'lar ko'p emas edi)
- **Fixed Files**:
  - `apps/catalog/views.py:29-40` — `_draft_counts` 4 query → 1 aggregate
  - `apps/commerce/views.py:354-371` — `AffiliateStatsView` 2 query → 1 aggregate
  - `apps/engagement/views.py:319-340` — `CurrentLeagueView` rank top-10'dan oldin, fallback bilan
  - `apps/engagement/views.py:394-398` — `NotificationListView` explicit order
  - `apps/commerce/views.py:66-69` — `WalletTransactionsView` explicit order_by
- **Acceptance Criteria**:
  - [x] `pip install nplusone django-perf-rec` dev.txt'ga qo'shildi
  - [x] `conftest.py`'ga `assert_max_queries(N)` fixture qo'shildi (CaptureQueriesContext orqali — nplusone signal false-positive ko'p)
  - [x] `apps/*/views.py` audit qilindi (Explore agent), top hotspot'lar tuzatildi
  - [x] `tests/integration/test_no_n_plus_1.py` — 6 ta test (catalog, leagues×2, notifications, transactions, affiliate)
  - [x] Test suite: 518 passed (512 + 6 yangi)
- **Audit Summary**: Loyiha exam serializer'larida allaqachon `select_related` ishlatilgan. Asosiy hotspot'lar — multiple `.count()` query'lar va duplicate filter chaqiruvlari (aggregate'ga birlashtirildi).
- **Reference**: ARCHITECTURE_REVIEW § 2.1

## ✅ ISSUE-102 — Celery beat distributed lock
- **Status**: ✅ **Done** (2026-05-16, direct commit)
- **Severity**: 🔴 P0
- **Effort**: 1 kun → real 1 soat
- **Implementation**:
  - `core/locks.py` — `single_runner_lock(name, expire)` context manager
  - Mexanizm: Redis SET NX EX + atomic check-and-delete (pipeline WATCH/MULTI)
  - Lua-siz — fakeredis bilan ham compatible
- **Wrapped tasks** (4 ta):
  - `engagement.check_broken_streaks` — TTL 30min (SMS dedup)
  - `engagement.leagues_weekly_recalc` — TTL 1h (wallet reward dedup)
  - `engagement.archive_leaderboards` — TTL 1h, per-period alohida lock
  - `commerce.auto_renew_subscriptions` — TTL 1h (charge dedup)
- **Acceptance Criteria**:
  - [x] Helper utility `core/locks.py`
  - [x] 4 ta scheduled task'ni o'rab chiqish
  - [x] Test: 7 ta `test_distributed_lock.py` (acquire/release/skip)
  - [x] Concurrent skip test — lock band bo'lsa task `{status: skipped_lock_busy}` qaytaradi
- **Reference**: ARCHITECTURE_REVIEW § 2.3

## ✅ ISSUE-103 — Soft-delete shim audit modellariga
- **Status**: ✅ **Done** (2026-05-16, direct commit)
- **Severity**: 🔴 P0 (GDPR + audit)
- **Effort**: 3-5 kun → real 2 soat (minimal-invasive approach)
- **Implementation**:
  - `core/mixins.py` — `SoftDeleteMixin` (is_deleted, deleted_at, pii_redacted + soft_delete() method + alive/deleted classmethods)
  - **Auto-filter YO'Q** — TenantManager bilan konflikt yo'q, audit view'lar default'da hammasini ko'radi. Application code `.alive()` yoki `.filter(is_deleted=False)` chaqiradi
- **Modellar** (6 ta):
  - `commerce.WalletTransaction` — pii_fields=('description',)
  - `commerce.PaymentIntent` — pii_fields=('metadata',)
  - `exams.ExamAttempt` — pii_fields=()
  - `exams.UserAnswer` — pii_fields=('selected',)
  - `intelligence.OpenEndedSubmission` — pii_fields=('content','human_notes')
  - `analytics.ExamEvent` — pii_fields=()
- **Migrations**: 4 ta (analytics, commerce, exams, intelligence) — `0002/0003_issue_103_soft_delete`
- **GDPR endpoint**: `POST /api/auth/gdpr/erasure/` (`apps/accounts/gdpr.py`)
  - User PII (email/phone/username/name) anonymize
  - Audit modellar soft_delete(redact_pii=True) — financial/compliance ID saqlanadi
  - Logout
- **Acceptance Criteria**:
  - [x] `core/mixins.py`'da SoftDeleteMixin
  - [x] 6 ta audit model uchun migration + apply
  - [x] GDPR endpoint stub (`/api/auth/gdpr/erasure/`)
  - [x] Test: 8 ta `test_gdpr_soft_delete.py` (mixin fields, soft_delete, redact_pii, alive/deleted helpers, endpoint flow)
  - [ ] FK cascade `CustomUser → audit FK` ni `CASCADE → SET_NULL`'ga o'zgartirish — alohida migration (kelajak: ISSUE-103b)
- **Reference**: ARCHITECTURE_REVIEW § 5.1

## 🔵 ISSUE-103 — Soft-delete shim audit modellariga
- **Status**: 🔵 **Planned**
- **Severity**: 🔴 P0 (GDPR + audit)
- **Effort**: 3-5 kun
- **Modellar**: `WalletTransaction`, `PaymentIntent`, `ExamEvent`, `OpenEndedSubmission`, `UserAnswer`, `ExamAttempt`
- **Tavsif**: GDPR Article 17 talabi: foydalanuvchi o'chirish → moliyaviy + audit data yo'qoladi. UZ buxgalteriya qonuni 5 yil retention talab qiladi.
- **Acceptance Criteria**:
  - [ ] `core/mixins.py`'da `SoftDeleteMixin`: `is_deleted=False`, `deleted_at=NULL`, `pii_redacted=False`
  - [ ] Migration: yuqoridagi 6 ta modelni mixin bilan extend qilish
  - [ ] `SoftDeleteManager` qo'shish: default queryset `.filter(is_deleted=False)`, `.all_with_deleted()` alohida
  - [ ] FK cascade'larni `SET_NULL` ga o'zgartirish (User → audit FK)
  - [ ] GDPR endpoint stub: `POST /api/gdpr/erasure/` — PII redact + audit ID saqlash
  - [ ] Tests: o'chirilgan user audit row'lari hali ham retrievable
- **Reference**: ARCHITECTURE_REVIEW § 5.1

## 🔵 ISSUE-104 — Custom Prometheus business metrics (5 ta minimum)
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1 (SRE ko'r holatda)
- **Effort**: 2-3 kun
- **Fayllar**: `core/metrics.py` (yangi), service layer'ga inject
- **Tavsif**: SRE'da business KPI ko'rinmaydi. Faqat HTTP/DB default metric'lar.
- **Acceptance Criteria**:
  - [ ] `core/metrics.py` — Prometheus metric ta'riflari
  - [ ] `yz_exams_submitted_total{org_id, is_public}` — finalize_attempt_score'da
  - [ ] `yz_payments_completed_total{provider, status}` — webhook handler'da
  - [ ] `yz_ai_tutor_seconds{model}` — tutor task'da Histogram
  - [ ] `yz_leaderboard_updates_total{scope}` — record_attempt'da
  - [ ] `yz_sms_sent_total{backend, status}` — notification fan-out'da
  - [ ] `monitoring/grafana/dashboards/business.json` — 5 panel
- **Reference**: ARCHITECTURE_REVIEW § 6.2, § 14

## 🔵 ISSUE-105 — `/health/ready/` tashqi dependency checks
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1
- **Effort**: 1 kun
- **Fayllar**: `core/health.py:13-46`
- **Tavsif**: Hozir faqat PG + Redis tekshiriladi. Anthropic/Telegram/Payme yo'q. Pod ready bo'lib turadi, lekin AI tutor o'lik.
- **Acceptance Criteria**:
  - [ ] Anthropic ping: `client.messages.create(model=..., max_tokens=1, messages=[{"role":"user","content":"."}])` 5s timeout
  - [ ] Telegram bot `getMe()` API call
  - [ ] Payme/Click stub mode'da skip (configurable)
  - [ ] Har check uchun individual status + jami `ready` faqat hammasi yashil
  - [ ] Test: failure simulation har bir dependency uchun
- **Reference**: ARCHITECTURE_REVIEW § 12 (12.1)

## 🔵 ISSUE-106 — OTP IP-level rate limit
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1 (xavfsizlik + SMS spam)
- **Effort**: 1 kun
- **Fayllar**: `apps/accounts/services/otp_service.py:48-64`
- **Tavsif**: Hozir per-phone limit (3/10min). Hujumchi 1000 telefon × 1 OTP = $4 SMS isrof, soatlik $96/kun.
- **Acceptance Criteria**:
  - [ ] IP-tier limit: `otp:send_count:ip:{ip}` — soatiga 20 ta
  - [ ] 5 IP send'dan keyin CAPTCHA gate (yoki harder challenge)
  - [ ] Test: simulate 1000 phone × 1 IP → 6-chi attempt 429
- **Reference**: ARCHITECTURE_REVIEW § 5.2

## 🔵 ISSUE-107 — Payment webhook signature security test
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1
- **Effort**: 0.5 kun
- **Fayllar**: `tests/security/test_payment_webhook_signature.py` (yangi)
- **Tavsif**: Production'da stub-mode signature qolib qolsa fail bo'lmaydi. Audit trap.
- **Acceptance Criteria**:
  - [ ] Test fail bo'ladi agar `DJANGO_ENV=prod` + `payment_providers._verify_signature` har qanday signature qabul qilsa
  - [ ] Test: invalid signature → 400
  - [ ] Test: valid signature + duplicate tx_id → idempotent 200
- **Reference**: ARCHITECTURE_REVIEW § 5.4

---

# Sprint 2-3 — Frontend unblock (BU OY)

> Frontend (Next.js + RN mobile) jamoa'ni unblock qilish.
> **Kuch**: 1-2 engineer × 2 hafta

## 🔵 ISSUE-201 — `drf-spectacular` + OpenAPI schema
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1 (frontend bloker)
- **Effort**: 2-3 kun
- **Fayllar**: `requirements/base.txt`, `core/settings/base.py`, `core/urls.py`
- **Tavsif**: Frontend jamoa view source kod o'qib field nom topadi (Lesson 12/15). Mobile RN dev'lar har release'da blocker.
- **Acceptance Criteria**:
  - [ ] `pip install drf-spectacular`
  - [ ] `SPECTACULAR_SETTINGS` qo'shish (title, version, schema_path)
  - [ ] URL: `/api/schema/`, `/api/schema/swagger/`, `/api/schema/redoc/`
  - [ ] Har view'ga `@extend_schema` decorator (input/output/responses)
  - [ ] CI: `python manage.py spectacular --validate --fail-on-warn`
- **Reference**: ARCHITECTURE_REVIEW § 9, § 1.2

## 🔵 ISSUE-202 — HTMX/REST view fayllarni ajratish
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1
- **Effort**: 3-5 kun
- **Fayllar**: Barcha `apps/*/views.py`
- **Tavsif**: HTMX (`View` + `request.POST`) va REST (`APIView` + `request.data`) bir faylda → arxitektura chegarasi yo'q.
- **Acceptance Criteria**:
  - [ ] Har app: `views.py` → `views/htmx.py` + `views/api.py`
  - [ ] URL'lar mos ravishda `urls/htmx.py` va `urls/api.py`
  - [ ] HTMX view'lar: `@ensure_csrf_cookie`, `request.POST`, returns partial template
  - [ ] REST view'lar: APIView, `request.data`, JSON Response
  - [ ] Lint qoidasi: faylida `APIView` va `View(View)` bir vaqtda bo'lmasligi kerak
- **Reference**: ARCHITECTURE_REVIEW § 1.2

## 🔵 ISSUE-203 — `/api/v1/` versioning
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1
- **Effort**: 1 kun
- **Fayllar**: `core/urls.py`, har `apps/*/urls/api.py`
- **Tavsif**: Hozirgi API path'larda version yo'q. Mobile app v1.5 production'da bo'lsa, breaking change = customer support to'lqini.
- **Acceptance Criteria**:
  - [ ] Yangi mount: `path('api/v1/exams/', include('apps.exams.urls.api'))`
  - [ ] Eski path'larni `path('api/exams/', ...)` 6 oy deprecate (Sunset header)
  - [ ] DRF settings: `DEFAULT_VERSIONING_CLASS = 'rest_framework.versioning.URLPathVersioning'`
  - [ ] Schema: ikkala version OpenAPI'da
- **Reference**: ARCHITECTURE_REVIEW § 3.3

## 🔵 ISSUE-204 — Response envelope standartlashtirish
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 2 kun
- **Fayllar**: `core/responses.py` (yangi), barcha API view'lar
- **Tavsif**: Hozir 3 ta envelope: `{count, results}`, `{count, transactions}`, bare list. Frontend'da 3 ta unmarshal yo'l.
- **Acceptance Criteria**:
  - [ ] `core/responses.py`: `success_response(data, meta=None)`, `error_response(code, message, details=None)`
  - [ ] Standart: `{success: bool, data: any, error: null|{code, message, details}, meta: null|{pagination, ...}}`
  - [ ] Custom DRF `EXCEPTION_HANDLER` yangi envelope bilan
  - [ ] Migration: har view'ni yangi helper'ga o'tkazish (sprintlarga bo'lib bo'lishi mumkin)
- **Reference**: ARCHITECTURE_REVIEW § 6 (Response)

## 🔵 ISSUE-205 — `django-fsm-2` qabul qilish (7 ta status model)
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1
- **Effort**: 3-5 kun
- **Modellar**: `MockExam`, `ExamAttempt`, `PracticeSession`, `QuestionDispute`, `OrganizationSubscription`, `PaymentIntent`, `Notification`
- **Tavsif**: State transition'lar view/service/task'lar bo'ylab tarqoq. Race condition risk + transition log yo'q.
- **Acceptance Criteria**:
  - [ ] `pip install django-fsm-2`
  - [ ] Har model uchun `status = FSMField()` migration
  - [ ] `@transition` decorator'lar — har ruxsat etilgan tranzitsiya uchun
  - [ ] Tests: `with pytest.raises(TransitionNotAllowed)` invalid transition'da
  - [ ] Optional: `django-fsm-log` — har transition audit qiladi
- **Reference**: ARCHITECTURE_REVIEW § 1.3

## 🔵 ISSUE-206 — API field naming consistency
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 2 kun
- **Fayllar**: Barcha `apps/*/serializers.py`
- **Tavsif**: `question_version` vs `question_version_id` aralash (Lesson 15). Convention kerak.
- **Acceptance Criteria**:
  - [ ] Convention tanlash: FK fields = `<name>_id` (Django ORM bilan mos)
  - [ ] DRF `PrimaryKeyRelatedField(source='question_version')` bilan `_id` suffix saqlash
  - [ ] Eski field nomlarni deprecate (6 oy ikkalasi qo'llab-quvvatlanadi)
  - [ ] OpenAPI schema'da deprecated marker
- **Reference**: LESSONS.md → Lesson 15

## 🔵 ISSUE-207 — WebSocket consumer explicit token validation
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2 (xavfsizlik)
- **Effort**: 1 kun
- **Fayllar**: `apps/exams/consumers.py:42-56`, `apps/engagement/consumers.py:46-66`
- **Tavsif**: `scope.get('user')` middleware'ga ishonadi. Refactor middleware → silent anonymous access risk.
- **Acceptance Criteria**:
  - [ ] `connect()`'da explicit `verify_access_token(token)` chaqirig'i
  - [ ] Middleware o'rniga consumer ichida token decode
  - [ ] Test: invalid/expired token → 4401 close
- **Reference**: ARCHITECTURE_REVIEW § 5.3

---

# Q2 — Scalability (KEYINGI 3 OY)

> 100K DAU'gacha scaling uchun.
> **Kuch**: 2 engineer × 3 oy

## 🔵 ISSUE-301 — Celery queue separation (critical/ai/batch/realtime)
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1
- **Effort**: 1 sprint
- **Fayllar**: `core/celery.py`, `charts/yuzdanyuz/templates/deployment-celery-worker-*.yaml`
- **Tavsif**: AI tutor (10s) `finalize_attempt_score` (50ms) bilan bir queue → exam submit lag.
- **Acceptance Criteria**:
  - [ ] `core/celery.py` `task_routes` config (4 queue)
  - [ ] Helm: 4 ta alohida worker deployment (critical, ai, batch, realtime)
  - [ ] Har worker uchun mos resource limits + concurrency
  - [ ] SLO: critical p95 < 1s, ai p95 < 30s, batch p95 < 5min
  - [ ] Prometheus alert har queue uchun
- **Reference**: ARCHITECTURE_REVIEW § 2.2

## 🔵 ISSUE-302 — KEDA queue depth autoscaling
- **Status**: 🔵 **Planned**
- **Severity**: 🟢 P3 (cost optimization)
- **Effort**: 3-5 kun
- **Fayllar**: `charts/yuzdanyuz/templates/keda-scaledobject.yaml` (yangi)
- **Tavsif**: Hozir always-on worker'lar. KEDA → queue empty bo'lganda scale-to-zero.
- **Acceptance Criteria**:
  - [ ] KEDA operator cluster'da install
  - [ ] Har worker queue uchun ScaledObject (Redis trigger)
  - [ ] minReplicas=0 batch queue uchun, =2 critical uchun
  - [ ] Cost saving observation 1 oy
- **Reference**: ARCHITECTURE_REVIEW § 19

## 🔵 ISSUE-303 — RLS qolgan 5 app'ga migration
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1
- **Effort**: 1 sprint
- **Apps**: `engagement`, `commerce`, `intelligence`, `analytics`, `organizations`
- **Tavsif**: Hozir RLS 13/46 jadval (28%). L3 defense yarim qurilgan.
- **Acceptance Criteria**:
  - [ ] Har app: yangi migration (template: `catalog.0003_enable_rls`)
  - [ ] Faqat `TenantTimestampMixin` ishlatadigan modellarga
  - [ ] Test: `tests/security/test_<app>_rls.py` — cross-tenant query bo'sh qaytaradi
  - [ ] CLAUDE.md'da RLS coverage 100%'ga yangilash
- **Reference**: ARCHITECTURE_REVIEW § 2.4

## 🔵 ISSUE-304 — `LeaderboardSnapshot.entries` normalizatsiya
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 1 sprint
- **Fayllar**: `apps/engagement/models.py`, migration, `archive_leaderboards` task
- **Tavsif**: JSONField'da top-1000 user. 5 yil = 1.3GB. "User X'ning Y haftadagi rank'i" so'rovi imkonsiz.
- **Acceptance Criteria**:
  - [ ] Yangi model: `LeaderboardEntry(snapshot, user, rank, score)`
  - [ ] Index: `(snapshot_id, user_id)`, `(snapshot_id, rank)`
  - [ ] Migration: eski JSON'ni yangi jadvalga ko'chirish
  - [ ] `archive_leaderboards`'ni qayta yozish (bulk_create entries)
  - [ ] `LeaderboardSnapshot.entries` field'ni deprecate (3 oy after migration)
- **Reference**: ARCHITECTURE_REVIEW § 6.3

## 🔵 ISSUE-305 — Service layer unit tests (≥80% qoplam)
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 2 sprint
- **Fayllar**: `tests/unit/test_<service>.py` (9 ta yangi)
- **Tavsif**: 13 ta service'dan 4 tasida direct unit test. Refactor xavfli.
- **Acceptance Criteria**:
  - [ ] `test_wallet_service.py`, `test_subscription_service.py`, `test_affiliate_service.py`
  - [ ] `test_streak_service.py`, `test_leagues_service.py`, `test_leaderboard.py`
  - [ ] `test_notifications_service.py`, `test_intelligence_services.py`, `test_analytics_services.py`
  - [ ] Coverage: `pytest --cov=apps.*.services --cov-fail-under=80`
- **Reference**: ARCHITECTURE_REVIEW § 4.1

## 🔵 ISSUE-306 — Composite + partial index migration
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 2-3 kun
- **Fayllar**: Har app `migrations/00XX_add_perf_indexes.py`
- **Tavsif**: Common access pattern'lar uchun index'lar yetishmaydi. PG query plan'lar sub-optimal.
- **Acceptance Criteria**:
  - [ ] `Notification(user, status, -created_at)` — composite
  - [ ] `WalletTransaction(wallet, kind, -created_at)` — composite
  - [ ] `ExamAttempt(exam, status, -started_at)` — composite
  - [ ] `ExamEvent(organization, user, subject_id, -completed_at)` — composite
  - [ ] `OrganizationSubscription` — partial WHERE `status IN ('active','trialing') AND auto_renew=true`
  - [ ] EXPLAIN ANALYZE before/after — query duration -50%+
- **Reference**: ARCHITECTURE_REVIEW § 8.1, § 8.2

## 🔵 ISSUE-307 — Caching layer (django cache + cachalot)
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 1 sprint
- **Fayllar**: `core/cache.py` (yangi), service layer
- **Tavsif**: Django cache framework hech qayerda ishlatilmaydi. Hot path'lar har request DB'ni uradi.
- **Acceptance Criteria**:
  - [ ] `pip install django-cachalot` ORM-darajadagi auto-cache
  - [ ] `core/cache.py` — custom `@cached` decorator (tenant-aware key prefix)
  - [ ] `Organization.get_effective_settings()` — TTL 300s
  - [ ] `OrgRole.permissions` parse — cache
  - [ ] `analytics.get_subject_averages` — TTL 60s
  - [ ] Invalidation signal'lar (Org/Role save → flush)
- **Reference**: ARCHITECTURE_REVIEW § 6.1, § 15

## 🔵 ISSUE-308 — Dead-letter queue Celery task'lar uchun
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 2-3 kun
- **Fayllar**: `core/celery.py`, yangi model `FailedTask`
- **Tavsif**: Hozir 3 marta fail bo'lsa task yo'qoladi. Reprocess imkonsiz.
- **Acceptance Criteria**:
  - [ ] `FailedTask` model (task_name, args, kwargs, exception, traceback, failed_at)
  - [ ] Global Celery `on_failure` handler — DLQ'ga yozish
  - [ ] Admin UI: "Reprocess" tugma
  - [ ] Prometheus metric: `yz_task_dlq_total{task_name}`
  - [ ] Test: simulate 4-chi fail → DLQ entry yaratiladi
- **Reference**: ARCHITECTURE_REVIEW § 16

---

# Q3 — Enterprise readiness (6 OY)

> $50K+/yil contract'lar uchun table-stakes.
> **Kuch**: 2-3 engineer × 3 oy

## 🔵 ISSUE-401 — Audit log (django-simple-history yoki custom)
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1 (SOC2 / ISO 27001 blocker)
- **Effort**: 1 sprint
- **Tavsif**: SOC2 "kim nimani qachon o'zgartirgan" talab qiladi. Hozir yo'q.
- **Acceptance Criteria**:
  - [ ] `pip install django-simple-history`
  - [ ] Critical modellar: Organization, CustomUser, OrgRole, Membership, SubscriptionPlan, OrganizationSubscription, Wallet, ExamAttempt, Question
  - [ ] Admin UI: history viewer
  - [ ] Retention policy: 7 yil (UZ buxgalteriya) / 2 yil (boshqalar)
  - [ ] PII redact eski history'da
- **Reference**: ARCHITECTURE_REVIEW § 10

## 🔵 ISSUE-402 — Soft-delete + GDPR erasure endpoint
- **Status**: 🔵 **Planned**
- **Severity**: 🔴 P0 (EU launch blocker)
- **Effort**: 2 sprint
- **Bog'lik**: ISSUE-103 (Sprint 1 shim)
- **Acceptance Criteria**:
  - [ ] `apps/gdpr/` yangi app: `ErasureRequest` model + service
  - [ ] `POST /api/v1/gdpr/erasure/` — request + token verification
  - [ ] Celery task: 30 kun keyin avtomat anonymize (PII redact, audit ID saqlash)
  - [ ] Email confirmation flow
  - [ ] Admin UI: pending erasure list + cancel
  - [ ] Compliance docs: data flow diagram
- **Reference**: ARCHITECTURE_REVIEW § 5.1, § 10

## 🔵 ISSUE-403 — SAML SSO support
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1 (B2B enterprise blocker)
- **Effort**: 1 sprint
- **Tavsif**: Enterprise: "biz Okta/Azure AD/OneLogin ishlatamiz". OAuth yetarli emas.
- **Acceptance Criteria**:
  - [ ] `pip install python3-saml` yoki `djangosaml2`
  - [ ] Per-organization SAML config (IdP metadata, certificate)
  - [ ] Admin UI: SAML setup wizard
  - [ ] Just-in-time provisioning (yangi user → Membership auto-create)
  - [ ] Test: Okta/Azure AD/OneLogin sandbox integratsiya
- **Reference**: ARCHITECTURE_REVIEW § 10

## 🔵 ISSUE-404 — Per-tenant config (Organization.settings UI)
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1
- **Effort**: 1 sprint
- **Fayllar**: `core/config.py`, magic constant'larni almashtirish
- **Bog'lik**: ISSUE-205 (FSM helps with per-tenant transitions)
- **Tavsif**: B2B: "1-strike-out kerak". Hozir hardcoded `MAX_STRIKES=3`.
- **Acceptance Criteria**:
  - [ ] `core/config.py`: `get_org_setting(org, key, default)` helper
  - [ ] Schema-typed config: `django-jsonform` yoki Pydantic
  - [ ] Admin UI: per-org settings editor (validatsiya bilan)
  - [ ] Migration: `MAX_STRIKES`, `HEARTBEAT_TIMEOUT_SECONDS`, `MILESTONE_REWARDS`, `PROMOTE_TOP`, `DEMOTE_BOTTOM`, `QUARANTINE_MIN_DISPUTES` — settings'ga ko'chirish
  - [ ] Audit: settings o'zgarishi history'da
- **Reference**: ARCHITECTURE_REVIEW § 3.1, § 11

## 🔵 ISSUE-405 — B2B Webhook tizimi (org-out webhook'lar)
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1
- **Effort**: 2 sprint
- **Tavsif**: Enterprise integratsiya: "exam tugasa, bizning LMS'ga POST yuboring".
- **Acceptance Criteria**:
  - [ ] `apps/webhooks/` yangi app
  - [ ] Model: `WebhookEndpoint(org, url, secret, events[])`, `WebhookDelivery(endpoint, event, status, attempts, response)`
  - [ ] Service: `dispatch_webhook(event, payload)` — HMAC-SHA256 signing
  - [ ] Retry: exponential backoff 1m, 5m, 30m, 2h, 12h
  - [ ] Dead-letter after 5 fails (ISSUE-308 bilan integratsiya)
  - [ ] Admin UI: endpoint CRUD + delivery log
- **Reference**: ARCHITECTURE_REVIEW § 10

## 🔵 ISSUE-406 — Bulk API operations
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 1 sprint
- **Tavsif**: Maktab onboarding 10K student = 10K REST chaqiruv. Imkonsiz.
- **Acceptance Criteria**:
  - [ ] `POST /api/v1/users/bulk/` — array of users, async Celery import
  - [ ] `POST /api/v1/questions/bulk/` — CSV/Excel upload, return job_id
  - [ ] `GET /api/v1/jobs/<job_id>/` — status polling
  - [ ] Validation: row-level errors qaytarish
- **Reference**: ARCHITECTURE_REVIEW § 10

## 🔵 ISSUE-407 — Long-lived API tokens (scoped)
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 1 sprint
- **Tavsif**: Server-to-server integratsiya. Cookie JWT yetarli emas.
- **Acceptance Criteria**:
  - [ ] Model: `APIToken(user, name, token_hash, scopes[], expires_at, last_used_at)`
  - [ ] DRF auth class: `APITokenAuthentication`
  - [ ] Scope-based permission: `@requires_scope('exams:read')`
  - [ ] Admin UI + self-service token management
  - [ ] Audit: har token usage log qilinadi
- **Reference**: ARCHITECTURE_REVIEW § 10

## 🔵 ISSUE-408 — Plugin/modular arxitektura (stevedore)
- **Status**: 🔵 **Planned**
- **Severity**: 🟢 P3
- **Effort**: 2 sprint
- **Fayllar**: `apps/commerce/payment_providers.py`, SMS backends, AI providers
- **Tavsif**: Yangi provider = kod commit. Enterprise plugin extension imkonsiz.
- **Acceptance Criteria**:
  - [ ] `pip install stevedore`
  - [ ] Entry points: `yuzdanyuz.payments`, `yuzdanyuz.sms`, `yuzdanyuz.ai`, `yuzdanyuz.anticheat`
  - [ ] Existing provider'larni entry-point ko'rinishida qayta yozish
  - [ ] Documentation: "How to write a plugin"
- **Reference**: ARCHITECTURE_REVIEW § 3.2, § 18

## 🔵 ISSUE-409 — `created_by`/`updated_by` audit fields
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2 (SOC2 prep)
- **Effort**: 1 sprint
- **Bog'liq**: ISSUE-401 (audit log)
- **Tavsif**: Hozir faqat `Organization.created_by`. WalletTransaction'da kim spend qildi — bilinmaydi.
- **Acceptance Criteria**:
  - [ ] `core/mixins.py`: `AuditUserMixin` (created_by, updated_by SET_NULL)
  - [ ] Critical modellar: WalletTransaction, Question, ExamAttempt, OrganizationSubscription
  - [ ] Auto-populate: `request.user` middleware orqali ContextVar'da
  - [ ] Migration: backfill `created_by = NULL` mavjud rows uchun
- **Reference**: ARCHITECTURE_REVIEW § 8.3

---

# Q4 — True production-grade (12 OY)

> 1M+ DAU, multi-region, SOC2 audit
> **Kuch**: 3-4 engineer × 3 oy

## 🔵 ISSUE-501 — ClickHouse real integratsiya (stub'dan keyin)
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 1 sprint
- **Bog'liq**: Hozirgi stub mode (`CLICKHOUSE_ENABLED=false`)
- **Acceptance Criteria**:
  - [ ] ClickHouse cluster deploy (managed: Altinity, Aiven, yoki self-host)
  - [ ] `analytics.clickhouse_client.py` real implementation
  - [ ] `ExamEvent` dual-write (PG + ClickHouse) — phased migration
  - [ ] Dashboard query'larni ClickHouse'ga ko'chirish
  - [ ] PG `ExamEvent` retention 90 kun, ClickHouse 7 yil
- **Reference**: ARCHITECTURE_REVIEW § 19

## 🔵 ISSUE-502 — CDC pipeline (Debezium → Kafka)
- **Status**: 🔵 **Planned**
- **Severity**: 🟢 P3 (multi-region prep)
- **Effort**: 1 sprint
- **Tavsif**: Multi-region replication uchun. Cross-region eventual consistency.
- **Acceptance Criteria**:
  - [ ] Kafka cluster (managed: Confluent yoki AWS MSK)
  - [ ] Debezium PostgreSQL connector
  - [ ] Topic: `yuzdanyuz.exams.attempt_status_change`, etc.
  - [ ] Consumer: cross-region replica writer
- **Reference**: ARCHITECTURE_REVIEW § 17

## 🔵 ISSUE-503 — `temporal.io` yoki KEDA ScaledJob (beat replacement)
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 2 sprint
- **Bog'liq**: ISSUE-102 (interim Redis lock fix)
- **Tavsif**: Beat single-replica fundamental cheklov. Temporal guaranteed-once.
- **Acceptance Criteria**:
  - [ ] PoC: 1 ta scheduled task (`check_broken_streaks`) → Temporal Workflow
  - [ ] Compare: Temporal vs KEDA ScaledJob (CronJob)
  - [ ] Migration: 7 ta beat task'ni 2-3 oy ichida ko'chirish
  - [ ] Beat decommission
- **Reference**: ARCHITECTURE_REVIEW § 2.3

## 🔵 ISSUE-504 — Multi-region active-passive
- **Status**: 🔵 **Planned**
- **Severity**: 🟢 P3
- **Effort**: 1 quarter
- **Tavsif**: EU read replica (GDPR) yoki Tashkent + EU.
- **Acceptance Criteria**:
  - [ ] PostgreSQL streaming replica EU'da
  - [ ] Read-only API endpoint'lar replica'ga route
  - [ ] CDN/edge routing (Cloudflare Workers)
  - [ ] Failover runbook + chaos engineering test
- **Reference**: ARCHITECTURE_REVIEW § 10

## 🔵 ISSUE-505 — Feature flag tizimi (django-flags yoki LaunchDarkly)
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 3-5 kun
- **Tavsif**: Hozir faqat env boolean'lar. A/B test, gradual rollout imkonsiz.
- **Acceptance Criteria**:
  - [ ] `pip install django-flags` (bepul) yoki LaunchDarkly SDK
  - [ ] Per-org, per-user, percentage rollout support
  - [ ] Admin UI flag toggle
  - [ ] Audit: flag o'zgarishi history'da
  - [ ] Migration: `ENABLE_AI_PARSER`, `ENABLE_REAL_PAYMENTS` flag'larga
- **Reference**: ARCHITECTURE_REVIEW § 11

## 🔵 ISSUE-506 — SOC2 Type 1 audit preparation
- **Status**: 🔵 **Planned**
- **Severity**: 🟠 P1 (enterprise contract'lar uchun)
- **Effort**: 2 quarter
- **Bog'liq**: ISSUE-401 (audit log), ISSUE-402 (GDPR), ISSUE-407 (API tokens)
- **Acceptance Criteria**:
  - [ ] Vendor selection (Drata, Vanta, Secureframe)
  - [ ] Access control policy documented
  - [ ] Incident response playbook
  - [ ] Backup + DR testing (quarterly)
  - [ ] Penetration test (annual)
  - [ ] SOC2 Type 1 report obtained
- **Reference**: ARCHITECTURE_REVIEW § 10

---

# 🔧 Cross-cutting issues (har sprint'da review qilish)

## 🔵 ISSUE-X01 — `services.py` placeholder fayllarni tozalash
- **Status**: 🔵 **Planned**
- **Severity**: 🟢 P3
- **Effort**: 1 kun
- **Fayllar**: `apps/exams/services.py`, `apps/catalog/services.py`, `apps/engagement/services.py`, `apps/organizations/services.py`
- **Acceptance Criteria**:
  - [ ] Yoki o'chirish, yoki real service kod ko'chirish
  - [ ] Project rule: "services.py bo'lsa, biznes mantig'i shu yerda yashashi shart"
- **Reference**: ARCHITECTURE_REVIEW § 4.2

## 🔵 ISSUE-X02 — Pre-commit hook'da fresh venv mirror CI
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 1 kun
- **Tavsif**: Hozir local venv state'ga tayanadi (Lesson 17 sababi).
- **Acceptance Criteria**:
  - [ ] Pre-push hook: temp venv yaratish, fresh `pip install -r requirements/base.txt -r requirements/dev.txt`
  - [ ] Yoki `nox` / `tox` adopt qilish
  - [ ] CI workflow ham bir xil pattern ishlatadi
- **Reference**: LESSONS.md → Lesson 17

## 🔵 ISSUE-X03 — Lazy import'larni explicit interface'ga ko'chirish
- **Status**: 🔵 **Planned**
- **Severity**: 🟢 P3
- **Effort**: 1 sprint
- **Fayllar**: `apps/engagement/streak_service.py:65`, `apps/engagement/leagues_service.py:127`
- **Tavsif**: `try/except` ichidagi lazy import = circular dependency riskini yashiradi.
- **Acceptance Criteria**:
  - [ ] `core/interfaces/reward.py` — `RewardService` abstract
  - [ ] Implementation: `apps/commerce/wallet_service.py` interface'ni implement qiladi
  - [ ] Engagement service interface'ga depend qiladi, implementation'ga emas
- **Reference**: ARCHITECTURE_REVIEW § 7.2

## 🔵 ISSUE-X04 — Edge cache + Anthropic prompt cache (cost optimization)
- **Status**: 🔵 **Planned**
- **Severity**: 🟢 P3
- **Effort**: 2-3 kun
- **Tavsif**: Cost saving. Cloudflare edge + Anthropic ephemeral cache.
- **Acceptance Criteria**:
  - [ ] `/sitemap.xml` Cache-Control: 1 soat
  - [ ] `/api/v1/leaderboard/global/?period=weekly` Cache-Control: 60s
  - [ ] Anthropic chaqiruvlarda `cache_control: ephemeral` system prompt'da
  - [ ] Cost dashboard before/after
- **Reference**: ARCHITECTURE_REVIEW § 15, § 19

## 🔵 ISSUE-X05 — `TenantManager` `unscoped_context()` audit test
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 1 kun
- **Tavsif**: Celery task'da context o'rnatishni unutsa, silent `.none()` qaytadi.
- **Acceptance Criteria**:
  - [ ] `tests/security/test_celery_tenant_context.py` — har Celery task explicit context bilan boshlanadi
  - [ ] Pytest marker `@requires_tenant_context` — task'ni text qiladi
  - [ ] Lint qoidasi: `@shared_task` function start'ida `tenant_context()` yoki `unscoped_context()` bo'lishi shart
- **Reference**: ARCHITECTURE_REVIEW § 13

## 🔵 ISSUE-X06 — JSON structured logging + log retention policy
- **Status**: 🔵 **Planned**
- **Severity**: 🟡 P2
- **Effort**: 2 kun
- **Tavsif**: Hozir string interpolation. Log aggregation'da search qiyin.
- **Acceptance Criteria**:
  - [ ] `python-json-logger` allaqachon bor — har `logger.info()` extra= kwarg bilan structured key
  - [ ] Helper: `log_event(level, event_name, **fields)` standartlash
  - [ ] Log retention: prod 30 kun (info), 90 kun (warning+), 1 yil (error)
  - [ ] Loki yoki CloudWatch retention policy config
- **Reference**: ARCHITECTURE_REVIEW § 14

---

# 🚀 Yangi feature kelajakda

> Audit'dan tashqari roadmap'da kelgan ish'lar
> Hozir Task 11+ uchun joy reserve qilingan

## 🔵 ISSUE-F01 — Frontend Next.js workstream (Tasks 4-9 UI)
- **Status**: 🔵 **Planned**
- **Priority**: User choice
- **Effort**: 2-3 quarter (alohida frontend team)

## 🔵 ISSUE-F02 — Real K8s deploy (Hetzner/DOKS cluster)
- **Status**: 🔵 **Planned**
- **Bog'liq**: ISSUE-302, ISSUE-303 (queue, RLS — prerequisite)
- **Effort**: 1 sprint

## 🔵 ISSUE-F03 — Production launch checklist (load test, security audit, backup drill)
- **Status**: 🔵 **Planned**
- **Bog'liq**: ISSUE-501, ISSUE-506 (ClickHouse, SOC2)
- **Effort**: 1 quarter

---

# 📅 Suggested timeline

```
2026-05 [SPRINT 1]   ████░░░░░░░░░░░░  P0 bleeding wounds (7 ta)
2026-06 [SPRINT 2-3] ██████████░░░░░░  Frontend unblock (6 ta)
2026-07 [Q2 start]   ░░░░░░████░░░░░░  Scalability sprint 1 (queue + RLS)
2026-08              ░░░░░░░░██████░░  Scalability sprint 2 (cache + indexes + DLQ)
2026-09              ░░░░░░░░░░██████  Service tests + LeaderboardSnapshot
2026-10 [Q3 start]   Audit log + SOC2 prep
2026-11              SAML + per-tenant config + webhooks
2026-12              Bulk API + API tokens + plugins
2027-Q1 [Q4]         ClickHouse + Temporal
2027-Q2              Multi-region + Feature flags + SOC2 Type 1
```

---

# 🎯 Bu hujjatdan qanday foydalanish

1. **Sprint planning oxirida**: Sprint 1 itemlarini GitHub Issue'larga ko'chirish — `gh issue create`'dan to'liq script keyinroq beraman
2. **Har issue completed bo'lsa**: Bu fayldan `🔵 Planned` → `✅ Done` ga o'tkazish + PR # yozish
3. **Yangi audit kelsa**: Yangi `ISSUE-NNN` qator qo'shish, sprint'ga joylash
4. **Har sprint oxirida**: "Umumiy progress" jadvalini yangilash
5. **Quarterly review**: Bu hujjat asosida CTO/founder bilan strategy meeting

---

# 🔗 Bog'liq hujjatlar

- [ARCHITECTURE_REVIEW_2026-05-16.md](ARCHITECTURE_REVIEW_2026-05-16.md) — to'liq audit (697 satr)
- [roadmap.md](roadmap.md) — Task 1-10 feature roadmap (allaqachon bajarilgan)
- [LESSONS.md](../../.claude/LESSONS.md) — 18 ta dars (Task 1-10 + E2E'dan)
- [CELERY_TASKS.md](CELERY_TASKS.md) — task inventory + schedule
- [DEPLOYMENT.md](DEPLOYMENT.md) — production lifecycle
- [DATABASE.md](DATABASE.md) — schema overview

---

> **Last updated**: 2026-05-16
> **Owner**: @narzullayevme (s.narzullayev@tassvision.ai)
> **Review cadence**: Sprint oxirida har 2 hafta + quarterly deep review
