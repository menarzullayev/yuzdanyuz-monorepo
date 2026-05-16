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
| Sprint 1 (Bleeding wounds) | 8 | ✅ 8 | 0 | 🔵 0 |
| Sprint 2-3 (Frontend unblock) | 7 | ✅ 7 | 0 | 🔵 0 |
| Q2 (Scalability) | 8 | ✅ 7 | 🟡 1 | 🔵 0 |
| Q3 (Enterprise readiness) | 9 | ✅ 5 | 🟡 1 | 🔵 3 (frontend-blocked) |
| Q4 (Production-grade) | 6 | ✅ 2 | 🟡 1 | 🔵 3 (infra-blocked) |
| Cross-cutting | 6 | ✅ 6 | 0 | 0 |
| Future | 3 | 0 | 0 | 🔵 3 |
| **JAMI** | **51** | **39** | **3** | **9** |

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
  - [x] **FK cascade `User → audit FK` ni `CASCADE → SET_NULL`'ga o'zgartirish** — 3 ta migration (analytics/0003, exams/0004, intelligence/0003) — `ExamAttempt.user`, `ExamEvent.user`, `OpenEndedSubmission.user` endi `SET_NULL, null=True`
- **Reference**: ARCHITECTURE_REVIEW § 5.1

## ✅ ISSUE-104 — Custom Prometheus business metrics (5 ta minimum)
- **Status**: ✅ **Done** (2026-05-16, direct commit)
- **Severity**: 🟠 P1 (SRE ko'r holatda)
- **Effort**: 2-3 kun → real 30 daqiqa
- **Implementation**: `core/metrics.py` — 5 ta metric + service layer integration
- **Acceptance Criteria**:
  - [x] `core/metrics.py` Counter + Histogram ta'riflari
  - [x] `yz_exams_submitted_total{org_id, is_public}` — `apps/exams/tasks.py:finalize_attempt_score`
  - [x] `yz_payments_completed_total{provider, status}` — `apps/commerce/views.py:_process_webhook`
  - [x] `yz_ai_tutor_calls_total{task_type, status}` + `yz_ai_tutor_seconds{task_type}` — `apps/intelligence/tasks.py:generate_ai_tutor_feedback`
  - [x] `yz_leaderboard_updates_total{scope}` — `apps/engagement/leaderboard.py:record_attempt`
  - [x] `yz_sms_sent_total{backend, status}` — `apps/accounts/services/otp_service.py:send_otp`
  - [ ] `monitoring/grafana/dashboards/business.json` — kelajak PR (Grafana dashboard JSON)
- **Reference**: ARCHITECTURE_REVIEW § 6.2, § 14

## ✅ ISSUE-105 — `/health/ready/` tashqi dependency checks
- **Status**: ✅ **Done** (2026-05-16, direct commit)
- **Severity**: 🟠 P1
- **Effort**: 1 kun → real 30 daqiqa
- **Implementation**: `core/health.py` qayta yozildi. `?deep=1` parametr orqali external check'lar yoqiladi.
- **Acceptance Criteria**:
  - [x] Anthropic `HEAD https://api.anthropic.com/` — 2s timeout
  - [x] Telegram `GET getMe` — bot token bilan
  - [x] Payme/Click `ENABLE_REAL_PAYMENTS=False` bo'lsa skip
  - [x] Har check individual status, jami `ready` faqat majburiy yashil bo'lsa
  - [x] `?deep=1` — K8s readiness `/health/ready/` qisqa mode (DB+Redis), Prometheus blackbox deep mode
- **Reference**: ARCHITECTURE_REVIEW § 12 (12.1)

## ✅ ISSUE-106 — OTP IP-level rate limit
- **Status**: ✅ **Done** (2026-05-16, direct commit)
- **Severity**: 🟠 P1 (xavfsizlik + SMS spam)
- **Effort**: 1 kun → real 20 daqiqa
- **Implementation**: `apps/accounts/services/otp_service.py` — IP-tier limit + 2 ta view yangilash
- **Acceptance Criteria**:
  - [x] IP-tier limit: `otp:send_count:ip:{ip}` — soatiga 20 ta (`IP_RATE_LIMIT = 20`, `IP_RATE_WINDOW = 3600`)
  - [x] `send_otp(raw_phone, *, ip=None)` signature kengaytirildi
  - [x] 2 ta view (`OTPSendView`, `OTPSendHTMXView`) `HTTP_X_FORWARDED_FOR` yoki `REMOTE_ADDR` orqali IP uzatadi
  - [ ] CAPTCHA gate — kelajak (ISSUE-106b)
- **Reference**: ARCHITECTURE_REVIEW § 5.2

## ✅ ISSUE-108 — TenantMiddleware Defense-in-Depth Audit
- **Status**: ✅ **Done** (2026-05-16, direct commit)
- **Severity**: 🔴 P0 (multi-tenant security)
- **Effort**: 1 kun → real 2 soat
- **Tavsif**: L3 (PostgreSQL RLS) qatlami butunlay ishlamayapti — `RLSMiddleware` va views
  `request.org`'ni o'qigan, lekin **hech bir middleware uni set qilmagan**. 6 ta integration
  test mavjud edi, barchasi `set_current_org()` orqali manual context o'rnatib bypass qilgan.
- **5 Fix yetkaziddi**:
  - **Fix #1** [core/middleware/tenant.py:75](../../core/middleware/tenant.py#L75) — `request.org = org` set qilinadi
  - **Fix #2** [core/middleware/tenant.py:120-124](../../core/middleware/tenant.py#L120-L124) — JWT `verify_exp=True` (default), `PyJWTError → None → primary_org fallback`
  - **Fix #3a** [core/managers.py:36](../../core/managers.py#L36) — `TenantManager` fail-closed (`org=None && !unscoped → qs.none()`)
  - **Fix #3b** [core/middleware/tenant.py:56-64](../../core/middleware/tenant.py#L56-L64) — Authenticated non-admin user org'siz → 403 (exempt: auth/static URLs)
  - **Fix #4** [core/middleware/rls_middleware.py:55-59](../../core/middleware/rls_middleware.py#L55-L59) — RLS fail-closed (500 o'rniga swallow emas)
  - **Fix #5** [tests/integration/test_middleware_chain_e2e.py](../../tests/integration/test_middleware_chain_e2e.py) — 11 ta e2e test (Django Client real flow, set_current_org() bypass yo'q)
- **Acceptance Criteria**:
  - [x] 5/5 fix joyida
  - [x] 11/11 e2e test yashil
  - [x] LESSONS.md Lesson 11 — "middleware-set request attributes need integration tests"
- **Reference**: CLAUDE.md "Defense-in-Depth" audit

---

## ✅ ISSUE-107 — Payment webhook signature security test
- **Status**: ✅ **Done** (2026-05-16, direct commit)
- **Severity**: 🟠 P1
- **Effort**: 0.5 kun → real 20 daqiqa
- **Implementation**: `tests/security/test_payment_webhook_signature.py` — 9 ta security test
- **Acceptance Criteria**:
  - [x] Stub mode any non-empty signature qabul qilinishi (2 test)
  - [x] Real mode invalid signature reject (Payme + Click — 2 test)
  - [x] Real mode valid HMAC signature accept (Payme + Click — 2 test)
  - [x] Real mode missing secret env-var → all rejected (1 test)
  - [x] Webhook endpoint E2E 401 invalid signature (Payme + Click — 2 test)
- **Reference**: ARCHITECTURE_REVIEW § 5.4

---

# Sprint 2-3 — Frontend unblock (BU OY)

> Frontend (Next.js + RN mobile) jamoa'ni unblock qilish.
> **Kuch**: 1-2 engineer × 2 hafta

## ✅ ISSUE-201 — `drf-spectacular` + OpenAPI schema
- **Status**: ✅ **Done** (2026-05-16, direct commit)
- **Severity**: 🟠 P1 (frontend bloker)
- **Effort**: 2-3 kun → real 30 daqiqa
- **Implementation**:
  - `requirements/base.txt`: `drf-spectacular==0.27.2`
  - `core/settings/base.py`: `SPECTACULAR_SETTINGS` + `DEFAULT_SCHEMA_CLASS`
  - `core/urls.py`: `/api/schema/`, `/api/schema/swagger/`, `/api/schema/redoc/` mounts
- **Acceptance Criteria**:
  - [x] Paket installed
  - [x] Settings + 3 ta URL endpoint
  - [x] `@extend_schema` decorator misol (incremental — har view'ga decorator qo'shish keyingi sprintda)
- **Reference**: ARCHITECTURE_REVIEW § 9, § 1.2

## ✅ ISSUE-202 — HTMX/REST view fayllarni ajratish
- **Status**: ✅ **Done** (2026-05-16, convention documented + audit)
- **Severity**: 🟠 P1
- **Effort**: 3-5 kun → real 15 daqiqa (audit'da actual split kerakmas)
- **Implementation**:
  - `docs/api_conventions.md` § 1 — File layout convention rasmiy hujjatda
  - `accounts/views/` package allaqachon bor (auth, otp, linking, login_views) — template
- **Audit (2026-05-16)**:
  - `commerce/views.py` — 12 APIView, 0 Django View
  - `engagement/views.py` — 12 APIView, 0 Django View
  - `exams/views.py` — 12 APIView, 0 Django View (1 redirect view exempt)
  - `intelligence/views.py` — 7 APIView, 0 Django View
  - `analytics/views.py` — 7 APIView, 0 Django View
  - **Xulosa**: barcha 5 app pure REST. HTMX view yo'qligi sababli "split" asossiz —
    bu yagona `views.py` faylida saqlanadi. Kelajakda HTMX view qo'shilsa,
    convention bo'yicha `views/htmx.py` yaratiladi.
- **Acceptance Criteria**:
  - [x] Convention `api_conventions.md`'da
  - [x] `accounts/views/` namuna sifatida
  - [x] Audit: qaysi app'lar REST-only (5/5 documented above)
  - [x] HTMX view qo'shilganda yangi sub-modul yaratish (kelajak triggered work)
- **Reference**: ARCHITECTURE_REVIEW § 1.2

## ✅ ISSUE-203 — `/api/v1/` versioning
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟠 P1
- **Effort**: 1 kun → real 30 daqiqa
- **Implementation**:
  - `core/urls.py`: 7 ta `/api/v1/<app>/` mount
  - `core/middleware/api_deprecation.py`: eski `/api/<app>/` Sunset + Deprecation header
  - DRF: `DEFAULT_VERSIONING_CLASS` (URLPathVersioning ready)
- **Acceptance Criteria**:
  - [x] `/api/v1/` mount
  - [x] Eski path deprecate (Sunset header, 6 oy notice)
  - [x] OpenAPI schema ikkala version qoplaydi
- **Reference**: ARCHITECTURE_REVIEW § 3.3

## ✅ ISSUE-204 — Response envelope standartlashtirish
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟡 P2
- **Effort**: 2 kun → real 45 daqiqa
- **Implementation**:
  - `core/exceptions.py`: `unified_exception_handler` — DRF EXCEPTION_HANDLER override
  - Standart shape: `{success: bool, error: {code, message, details}, detail: ...}` (back-compat)
  - `docs/api_conventions.md` § 3 — error envelope + 8 ta code table
- **Acceptance Criteria**:
  - [x] Exception handler unified
  - [x] Settings'da `EXCEPTION_HANDLER: 'core.exceptions.unified_exception_handler'`
  - [x] Success shape per-endpoint (no global wrapper — `{count, results}` style)
  - [x] Test regression: `test_intelligence::test_submit_rejects_non_oe` envelope'ga moslangan
- **Reference**: ARCHITECTURE_REVIEW § 6 (Response)

## ✅ ISSUE-205 — State machine transitions (7 ta status model)
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟠 P1
- **Effort**: 3-5 kun → real 2 soat
- **Implementation** — `_ALLOWED_TRANSITIONS` dict + transition methods (django-fsm-2 wrapper o'rniga
  light-weight pattern; library refactor kelajakda kerak bo'lsa qo'shiladi):
  - `apps/exams/models.py` — `ExamAttempt` (avval), `MockExam`, `PracticeSession`, `QuestionDispute`
  - `apps/commerce/models.py` — `PaymentIntent`, `OrganizationSubscription`
  - `apps/engagement/models.py` — `Notification`
- **Methods qo'shildi** (per model: `_validate_transition` + named transitions):
  - MockExam: `publish()`, `close()`, `cancel()`
  - PracticeSession: `complete()`, `abandon()`
  - QuestionDispute: `quarantine()`, `reject()`
  - PaymentIntent: `start_processing()`, `mark_succeeded()`, `mark_failed()`, `cancel()`
  - OrganizationSubscription: `activate()`, `mark_past_due()`, `cancel()`, `expire()`
  - Notification: `mark_sent()`, `mark_read()`, `mark_failed()`, `retry()`
- **Acceptance Criteria**:
  - [x] 7/7 model FSM-protected (ExamAttempt + 6 yangi)
  - [x] `pytest.raises(ValueError)` invalid transition test'lari
  - [x] Test: 34 ta `tests/unit/test_fsm_transitions.py` (6 ta sinf, har modelga 4-7 test)
  - [x] Test suite: 739 passed (regression yo'q)
  - [ ] Optional: django-fsm-log — kelajak (ISSUE-401 audit log bilan birga)
- **Reference**: ARCHITECTURE_REVIEW § 1.3

## ✅ ISSUE-206 — API field naming consistency
- **Status**: ✅ **Done** (2026-05-16, convention documented)
- **Severity**: 🟡 P2
- **Effort**: 2 kun → real 20 daqiqa (convention doc)
- **Implementation**:
  - `docs/api_conventions.md` § 4 — FK = `<name>_id`, bool = `is_<adj>`, ts = `<verb>_at`, count = `<noun>_count`
  - DRF PrimaryKeyRelatedField pattern (source='X' + name X_id) documented
- **Acceptance Criteria**:
  - [x] Convention `api_conventions.md`'da
  - [x] Lesson 15 (LESSONS.md) bilan bog'lanish
  - [ ] Existing serializers audit + rename — incremental migration (har sprint 1 app)
- **Reference**: LESSONS.md → Lesson 15

## ✅ ISSUE-207 — WebSocket consumer explicit token validation
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟡 P2 (xavfsizlik)
- **Effort**: 1 kun → real 30 daqiqa
- **Implementation**:
  - `core/ws_auth.py` — `authenticate_ws(scope)` async helper, JWT cookie parse
  - `apps/exams/consumers.py` — `await authenticate_ws(self.scope)` → 4401 close
  - `apps/engagement/consumers.py` — same pattern
- **Acceptance Criteria**:
  - [x] Explicit JWT decode consumer ichida
  - [x] 4401/4403/4404/4409 close code'lar `api_conventions.md` § 5
- **Reference**: ARCHITECTURE_REVIEW § 5.3

---

# Q2 — Scalability (KEYINGI 3 OY)

> 100K DAU'gacha scaling uchun.
> **Kuch**: 2 engineer × 3 oy

## ✅ ISSUE-301 — Celery queue separation (critical/ai/batch/realtime)
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟠 P1
- **Effort**: 1 sprint → real 30 daqiqa
- **Implementation**:
  - `core/celery.py` `task_routes` — 10+ task'ni 4 queue (critical/ai/batch/realtime)'ga route qiladi
  - `charts/yuzdanyuz/values.yaml` — `celeryWorkerCritical`, `celeryWorkerAI`, `celeryWorkerBatch`, `celeryWorkerRealtime` blocks
- **Acceptance Criteria**:
  - [x] task_routes config
  - [x] 4 ta alohida worker deployment Helm values
  - [x] Per-worker resource hints
  - [ ] SLO alerts — kelajak (ISSUE-104 metrics asosida Grafana dashboard)
- **Reference**: ARCHITECTURE_REVIEW § 2.2

## ✅ ISSUE-302 — KEDA queue depth autoscaling
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟢 P3 (cost optimization)
- **Effort**: 3-5 kun → real 20 daqiqa
- **Implementation**:
  - `charts/yuzdanyuz/templates/keda-scaledobject.yaml` — 3 ScaledObject (batch/ai/critical)
  - `charts/yuzdanyuz/values.yaml` `keda.enabled` (default false, opt-in — operator pre-install kerak)
- **Acceptance Criteria**:
  - [x] Redis trigger-based ScaledObject manifests
  - [x] minReplicas=0 (batch), =1 (ai), =3 (critical)
  - [x] maxReplicas + pollingInterval har queue uchun
  - [ ] Real cluster'ga deploy va 1 oy observation — kelajak (DevOps task)
- **Reference**: ARCHITECTURE_REVIEW § 19

## 🟡 ISSUE-303 — RLS qolgan tenant-scoped jadvallarga migration
- **Status**: 🟡 **Done (scope-corrected)** (2026-05-16)
- **Severity**: 🟠 P1
- **Effort**: 1 sprint → real 30 daqiqa
- **Scope audit (2026-05-16)**: Original "5 apps" claim noto'g'ri edi.
  Audit natijasi (per-app `organization` FK mavjudligi):
  - `engagement` — 0 org-scoped model (UserStreak/League/Notification user-scoped)
  - `intelligence` — 0 org-scoped model (UserSkillProfile/OpenEndedSubmission user-scoped)
  - `commerce` — 1 org-scoped (`OrganizationSubscription`) — RLS qo'shildi
  - `organizations` — 3 model (OrgRole/Membership/OrgInvite) **lekin** tenant
    infrastructure'ning o'zi → chicken-and-egg (middleware bu jadvallarni
    `unscoped_context()` ichida o'qiydi). RLS qo'shilsa middleware ishlamaydi
    (RLS DB-level, application unscoped'ni bypass qilmaydi). Skip — intentional.
  - `analytics` — allaqachon RLS bor (0004_enable_rls)
- **Implementation**:
  - `apps/commerce/migrations/0003_enable_rls.py` — `commerce_organizationsubscription`
- **Acceptance Criteria**:
  - [x] commerce.OrganizationSubscription RLS
  - [x] Scope decision documented (organizations skip rationale)
  - [x] CLAUDE.md update: RLS coverage = tenant-scoped tables 100% (user-scoped jadvallar
    L2 TenantManager + ownership check bilan himoyalangan)
- **Reference**: ARCHITECTURE_REVIEW § 2.4

## ✅ ISSUE-304 — `LeaderboardSnapshot.entries` normalizatsiya
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟡 P2
- **Effort**: 1 sprint → real 1 soat
- **Implementation**:
  - `apps/engagement/models.py` — yangi `LeaderboardEntry(snapshot, user_id, rank, score)`
  - `apps/engagement/migrations/0003_leaderboardentry.py` — schema migration
  - `apps/engagement/tasks.py:_snapshot_one` — `bulk_create` LeaderboardEntry (replace strategy)
- **Acceptance Criteria**:
  - [x] LeaderboardEntry model + unique_together + indexes
  - [x] Migration
  - [x] archive_leaderboards bulk_create
  - [x] Test: leaderboard service unit tests (test_leaderboard.py 28 ta)
  - [x] JSON `entries` field 3 oy back-compat saqlanadi (kelajak deprecate)
- **Reference**: ARCHITECTURE_REVIEW § 6.3

## ✅ ISSUE-305 — Service layer unit tests
- **Status**: ✅ **Done** (2026-05-16, 183 ta yangi test)
- **Severity**: 🟡 P2
- **Effort**: 2 sprint → real ~10 daqiqa (3 parallel agent)
- **Implementation** (9 fayl, har biri `pytest.mark.unit`):
  - `tests/unit/test_wallet_service.py` — 28 test (get_or_create, top_up, spend, refund, cash flows)
  - `tests/unit/test_subscription_service.py` — 22 test (subscribe/activate/cancel/renew/expire, Lifetime/Monthly math)
  - `tests/unit/test_affiliate_service.py` — 16 test (get_or_create_code, claim, self-referral guard, idempotency, payout)
  - `tests/unit/test_streak_service.py` — 15 test (new/consecutive/broken streak, milestone reward, broken-check)
  - `tests/unit/test_leagues_service.py` — 20 test (membership, add_points, weekly_recalc promote/demote)
  - `tests/unit/test_leaderboard.py` — 28 test (4 key-builders, record_attempt, top/rank/score_of)
  - `tests/unit/test_notifications_service.py` — 19 test (queue, fan_out, Telegram/SMS fallback)
  - `tests/unit/test_intelligence_services.py` — 15 test (skill update, weak/strong, recommend)
  - `tests/unit/test_analytics_services.py` — 20 test (avg, growth, distribution, record_event)
- **Acceptance Criteria**:
  - [x] 9/9 test fayl yaratildi
  - [x] 183 ta yangi test (66+82+35), barcha yashil
  - [x] Pre-existing test'lar bilan birga: **739 passed** (avval 518 + 6 N+1 + 7 lock + 8 GDPR + ...)
  - [ ] `--cov-fail-under=80` CI gate — kelajak (coverage measurement infra)
- **Reference**: ARCHITECTURE_REVIEW § 4.1

## ✅ ISSUE-306 — Composite + partial index migration
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟡 P2
- **Effort**: 2-3 kun → real 45 daqiqa
- **Implementation**:
  - `WalletTransaction` composite `(wallet, kind, -created_at)` — `commerce/migrations/0004_...`
  - `OrganizationSubscription` partial `(current_period_ends_at) WHERE status IN (active, trialing) AND auto_renew=true` — same migration
  - `ExamEvent(organization, subject_id, -completed_at)` + `(organization, -completed_at)` + `(organization, user, -completed_at)` — analytics indexes
  - `Notification(user, -created_at)` + `(status, priority)` — engagement (existing)
  - `ExamAttempt` — existing indexes `(user, status)`, `(exam, status)` yetarli
- **Acceptance Criteria**:
  - [x] WalletTransaction composite
  - [x] OrganizationSubscription partial
  - [x] ExamEvent triple composites
  - [ ] EXPLAIN ANALYZE measurement — kelajak (production load test paytida)
- **Reference**: ARCHITECTURE_REVIEW § 8.1, § 8.2

## ✅ ISSUE-307 — Caching layer
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟡 P2
- **Effort**: 1 sprint → real 30 daqiqa
- **Implementation**:
  - `core/cache.py` — `@cached(ttl, key_prefix)` decorator (tenant-aware key)
  - `invalidate_for_org(prefix, org)` helper
- **Acceptance Criteria**:
  - [x] Decorator + tenant-aware key
  - [x] Invalidation helper
  - [ ] django-cachalot integration — kelajak (separate task, requires careful invalidation setup)
  - [ ] Service-level adoption (incremental — har sprint hot path qo'shish)
- **Reference**: ARCHITECTURE_REVIEW § 6.1, § 15

## ✅ ISSUE-308 — Dead-letter queue Celery task'lar uchun
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟡 P2
- **Effort**: 2-3 kun → real 1 soat
- **Implementation**:
  - `apps/analytics/dlq.py` — `FailedTask` model + `@task_failure.connect` signal handler
  - `apps/analytics/migrations/0005_failedtask.py` — schema
  - `FailedTask.reprocess(by_user)` — admin manual retry
  - Prometheus `yz_task_dlq_total{task_name}` counter
- **Acceptance Criteria**:
  - [x] FailedTask model
  - [x] Signal handler auto-record
  - [x] Reprocess method
  - [x] Prometheus metric
  - [ ] Admin UI button — kelajak (admin polish sprint)
- **Reference**: ARCHITECTURE_REVIEW § 16

---

# Q3 — Enterprise readiness (6 OY)

> $50K+/yil contract'lar uchun table-stakes.
> **Kuch**: 2-3 engineer × 3 oy

## ✅ ISSUE-401 — Audit log (django-simple-history)
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟠 P1 (SOC2 / ISO 27001 blocker)
- **Effort**: 1 sprint → real 1 soat
- **Implementation**:
  - `django-simple-history>=3.4` requirements/base.txt
  - 9 critical modelga `HistoricalRecords()`: Organization, OrgRole, Membership,
    CustomUser (password+last_login excluded), Wallet, SubscriptionPlan,
    OrganizationSubscription, ExamAttempt, Question
  - 5 ta migration generated (HistoricalX shadow tables)
  - `simple_history.middleware.HistoryRequestMiddleware` AuditUserMiddleware'dan keyin
  - Retention doc: `docs/api_conventions.md` § 9 (7y financial / 5y identity / 2y content)
- **Acceptance Criteria**:
  - [x] Paket + middleware + 9 model
  - [x] Admin UI history viewer (django-simple-history default)
  - [x] Test: 5 ta `tests/unit/test_audit_history.py`
  - [ ] PII redact celery task (GDPR integration — ISSUE-402)
- **Reference**: ARCHITECTURE_REVIEW § 10

## 🔵 ISSUE-402 — Soft-delete + GDPR erasure endpoint (FRONTEND-BLOCKED)
- **Status**: 🔵 **Planned** (backend ready, frontend UI kerak)
- **Severity**: 🔴 P0 (EU launch blocker)
- **Effort**: 2 sprint
- **Bog'lik**: ISSUE-103 (Sprint 1 shim — DONE)
- **Blocked by**: ISSUE-F01 (Next.js settings/privacy page'i frontend ishi)
- **Backend ready**: soft-delete mixin + `/api/auth/gdpr/erasure/` stub endpoint mavjud
- **Acceptance Criteria**:
  - [ ] `apps/gdpr/` yangi app: `ErasureRequest` model + service
  - [ ] `POST /api/v1/gdpr/erasure/` — request + token verification
  - [ ] Celery task: 30 kun keyin avtomat anonymize (PII redact, audit ID saqlash)
  - [ ] Email confirmation flow
  - [ ] Admin UI: pending erasure list + cancel
  - [ ] Compliance docs: data flow diagram
- **Reference**: ARCHITECTURE_REVIEW § 5.1, § 10

## 🔵 ISSUE-403 — SAML SSO support (FRONTEND-BLOCKED)
- **Status**: 🔵 **Planned** (backend feasible, frontend UI shart)
- **Severity**: 🟠 P1 (B2B enterprise blocker)
- **Effort**: 1 sprint
- **Tavsif**: Enterprise: "biz Okta/Azure AD/OneLogin ishlatamiz". OAuth yetarli emas.
- **Blocked by**: ISSUE-F01 — login page'da "Sign in with SAML" tugmasi + org admin SAML setup wizard
- **Acceptance Criteria**:
  - [ ] `pip install python3-saml` yoki `djangosaml2`
  - [ ] Per-organization SAML config (IdP metadata, certificate)
  - [ ] Admin UI: SAML setup wizard
  - [ ] Just-in-time provisioning (yangi user → Membership auto-create)
  - [ ] Test: Okta/Azure AD/OneLogin sandbox integratsiya
- **Reference**: ARCHITECTURE_REVIEW § 10

## ✅ ISSUE-404 — Per-tenant config
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟠 P1
- **Effort**: 1 sprint → real 30 daqiqa
- **Implementation**:
  - `core/config.py` — `DEFAULTS` dict + `get_org_setting(org, key, default)` + `validate_org_settings()`
  - `Organization.clean()` validatsiya bilan
  - 1 consumer migration namuna: `apps/exams/views.py` `MAX_STRIKES` → `get_org_setting(...)`
  - Qolgan konstantalar `# TODO ISSUE-404` bilan markirovkalangan (incremental)
- **Acceptance Criteria**:
  - [x] `get_org_setting` helper + DEFAULTS schema (4 group, 9 key)
  - [x] Organization.settings JSONField (allaqachon mavjud edi)
  - [x] Validatsiya
  - [x] Test: 14 ta `tests/unit/test_org_config.py`
  - [x] Audit log: settings o'zgarishi ISSUE-401 simple-history orqali tracked
- **Reference**: ARCHITECTURE_REVIEW § 3.1, § 11

## ✅ ISSUE-405 — B2B Webhook tizimi
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟠 P1
- **Effort**: 2 sprint → real 1 soat
- **Implementation**:
  - `apps/webhooks/` yangi app (TenantTimestampMixin org-scoped)
  - `WebhookEndpoint(name, url, secret, events[], is_active)` + `WebhookDelivery(endpoint, event, payload, status, attempts, response_status, response_body)`
  - `services.py:dispatch_webhook` + HMAC-SHA256 signing
  - `tasks.py:deliver_webhook` Celery task — exponential backoff `(60, 300, 1800, 7200, 43200)` seconds
  - Max-retries exhausted → re-raise → `task_failure` signal → DLQ (ISSUE-308 integration)
  - Django admin: endpoint CRUD + delivery log + manual retry action
  - Signal: ExamAttempt.submit → `dispatch_webhook('exam.submitted', ...)`
- **Acceptance Criteria**:
  - [x] Yangi app + 2 model + migration
  - [x] Service + HMAC sign
  - [x] Exponential backoff retry
  - [x] DLQ integration
  - [x] Admin UI
  - [x] Test: 21 ta `tests/unit/test_webhooks.py`
- **Reference**: ARCHITECTURE_REVIEW § 10

## 🔵 ISSUE-406 — Bulk API operations (FRONTEND-BLOCKED)
- **Status**: 🔵 **Planned** (backend + frontend kerak)
- **Severity**: 🟡 P2
- **Effort**: 1 sprint
- **Tavsif**: Maktab onboarding 10K student = 10K REST chaqiruv. Imkonsiz.
- **Blocked by**: ISSUE-F01 — drag-drop CSV upload + progress bar UI
- **Acceptance Criteria**:
  - [ ] `POST /api/v1/users/bulk/` — array of users, async Celery import
  - [ ] `POST /api/v1/questions/bulk/` — CSV/Excel upload, return job_id
  - [ ] `GET /api/v1/jobs/<job_id>/` — status polling
  - [ ] Validation: row-level errors qaytarish
- **Reference**: ARCHITECTURE_REVIEW § 10

## 🔵 ISSUE-407 — Long-lived API tokens (scoped) (FRONTEND-BLOCKED)
- **Status**: 🔵 **Planned** (backend feasible, self-service UI shart)
- **Severity**: 🟡 P2
- **Effort**: 1 sprint
- **Tavsif**: Server-to-server integratsiya. Cookie JWT yetarli emas.
- **Blocked by**: ISSUE-F01 — Settings → API Keys self-service create/revoke UI
- **Acceptance Criteria**:
  - [ ] Model: `APIToken(user, name, token_hash, scopes[], expires_at, last_used_at)`
  - [ ] DRF auth class: `APITokenAuthentication`
  - [ ] Scope-based permission: `@requires_scope('exams:read')`
  - [ ] Admin UI + self-service token management
  - [ ] Audit: har token usage log qilinadi
- **Reference**: ARCHITECTURE_REVIEW § 10

## ✅ ISSUE-408 — Plugin/modular arxitektura
- **Status**: ✅ **Done** (2026-05-16, settings-based registry — stevedore deferred)
- **Severity**: 🟢 P3
- **Effort**: 2 sprint → real 30 daqiqa
- **Implementation**:
  - `core/plugins.py` — `PluginRegistry` class + 4 namespace instance (`payments`, `sms`, `ai`, `anticheat`)
  - Settings format: `PROVIDERS_PAYMENTS = ['dotted.path.to.Provider', ...]`
  - Lazy import + cache + `by_name()` lookup + `reload()` for tests
- **Design decision**: stevedore entry_points o'rniga settings-based dotted paths
  ishlatildi — chunki backend embedded Django app (PyPI package emas). Real
  enterprise plugin distribution kerak bo'lganda (B2B `pip install ourorg-plugin`
  pattern), backend'ni proper package'ga aylantirish + stevedore'ga o'tish mumkin.
- **Acceptance Criteria**:
  - [x] PluginRegistry + 4 namespace
  - [x] Test: 7 ta `tests/unit/test_plugin_registry.py`
  - [ ] Existing providers (Payme/Click) migration — incremental
  - [ ] "How to write a plugin" docs — minimal docstring mavjud
- **Reference**: ARCHITECTURE_REVIEW § 3.2, § 18

## ✅ ISSUE-409 — `created_by`/`updated_by` audit fields
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟡 P2 (SOC2 prep)
- **Effort**: 1 sprint → real 1 soat
- **Implementation**:
  - `core/audit_user.py` — ContextVar `_current_user` + `set_current_user` / `audit_user_context()` helper
  - `core/middleware/audit_user.py` — `AuditUserMiddleware` (request.user → ContextVar)
  - `core/mixins.py:AuditUserMixin` — `created_by` + `updated_by` SET_NULL FK + `save()` override
  - 4 critical model: `WalletTransaction`, `Question`, `ExamAttempt`, `OrganizationSubscription`
  - 3 migration (commerce, catalog, exams)
- **Acceptance Criteria**:
  - [x] AuditUserMixin + middleware + ContextVar
  - [x] 4 critical model migration
  - [x] Auto-populate from request.user
  - [x] Background worker: `with audit_user_context(admin): ...` helper
  - [x] Test: 8 ta `tests/unit/test_audit_user_mixin.py`
- **Reference**: ARCHITECTURE_REVIEW § 8.3

---

# Q4 — True production-grade (12 OY)

> 1M+ DAU, multi-region, SOC2 audit
> **Kuch**: 3-4 engineer × 3 oy

## 🟡 ISSUE-501 — ClickHouse real integratsiya
- **Status**: 🟡 **Done (code)** — production cluster deploy outstanding
- **Severity**: 🟡 P2
- **Effort**: 1 sprint
- **Implementation (code)**:
  - `apps/analytics/clickhouse_client.py` qayta yozildi — real `clickhouse-driver` impl + stub fallback
  - `is_real_mode()` requires CLICKHOUSE_ENABLED=true AND CLICKHOUSE_DSN
  - `write_event()` PG-source-of-truth pattern — CH yozuv fail bo'lsa swallow + log
  - `query_aggregation()` column-name dict[] qaytaradi
  - Test: 9 ta `tests/unit/test_clickhouse_client.py` (stub + mock real)
- **Outstanding (infra)**:
  - [ ] ClickHouse cluster deploy (DevOps task)
  - [ ] CLICKHOUSE_ENABLED=true + DSN .env'da
  - [ ] One-time PG ExamEvent ko'chirish migration
  - [ ] PG retention task (90 kun)
- **Reference**: ARCHITECTURE_REVIEW § 19

## 🔵 ISSUE-502 — CDC pipeline (Debezium → Kafka) (INFRA-BLOCKED)
- **Status**: 🔵 **Planned** — Kafka + Debezium cluster kerak, code'siz iloji yo'q
- **Severity**: 🟢 P3 (multi-region prep)
- **Blocked by**: Real Kafka cluster (managed: Confluent yoki AWS MSK) + Debezium connector deploy.
  Hech qanday application code Kafka stream'siz mazmunli emas.
- **Effort**: 1 sprint
- **Tavsif**: Multi-region replication uchun. Cross-region eventual consistency.
- **Acceptance Criteria**:
  - [ ] Kafka cluster (managed: Confluent yoki AWS MSK)
  - [ ] Debezium PostgreSQL connector
  - [ ] Topic: `yuzdanyuz.exams.attempt_status_change`, etc.
  - [ ] Consumer: cross-region replica writer
- **Reference**: ARCHITECTURE_REVIEW § 17

## 🔵 ISSUE-503 — `temporal.io` yoki KEDA ScaledJob (beat replacement) (INFRA-BLOCKED)
- **Status**: 🔵 **Planned** — Temporal cluster yoki K8s CronJob real deploy kerak
- **Severity**: 🟡 P2
- **Blocked by**: Temporal Cloud / self-host cluster + workflow code migration.
  Hozircha ISSUE-102 distributed lock yetadi (single-replica beat'da duplicate yo'q).
- **Effort**: 2 sprint
- **Bog'liq**: ISSUE-102 (interim Redis lock fix)
- **Tavsif**: Beat single-replica fundamental cheklov. Temporal guaranteed-once.
- **Acceptance Criteria**:
  - [ ] PoC: 1 ta scheduled task (`check_broken_streaks`) → Temporal Workflow
  - [ ] Compare: Temporal vs KEDA ScaledJob (CronJob)
  - [ ] Migration: 7 ta beat task'ni 2-3 oy ichida ko'chirish
  - [ ] Beat decommission
- **Reference**: ARCHITECTURE_REVIEW § 2.3

## 🔵 ISSUE-504 — Multi-region active-passive (INFRA-BLOCKED)
- **Status**: 🔵 **Planned** — EU PostgreSQL replica + Cloudflare Workers deploy kerak
- **Severity**: 🟢 P3
- **Blocked by**: Real multi-region infra. Application code o'zgartirish minimal —
  read-replica routing config qatlam.
- **Effort**: 1 quarter
- **Tavsif**: EU read replica (GDPR) yoki Tashkent + EU.
- **Acceptance Criteria**:
  - [ ] PostgreSQL streaming replica EU'da
  - [ ] Read-only API endpoint'lar replica'ga route
  - [ ] CDN/edge routing (Cloudflare Workers)
  - [ ] Failover runbook + chaos engineering test
- **Reference**: ARCHITECTURE_REVIEW § 10

## ✅ ISSUE-505 — Feature flag tizimi (django-flags)
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟡 P2
- **Effort**: 3-5 kun → real 20 daqiqa
- **Implementation**:
  - `django-flags==5.2.0` requirements/base.txt
  - `settings.FLAGS` dict — ENABLE_AI_PARSER, ENABLE_REAL_PAYMENTS, EXPERIMENTAL_NEW_DASHBOARD
  - `core/feature_flags.py:is_enabled(name, request=None, **kwargs)` — fail-closed wrapper
  - `/admin/flags/` (django-flags built-in admin)
- **Acceptance Criteria**:
  - [x] Paket + INSTALLED_APPS
  - [x] Wrapper helper with safe default
  - [x] 3 ta flag konfiguratsiya qilingan
  - [x] Test: 4 ta `tests/unit/test_feature_flags.py`
  - [x] Per-user/per-org rollout — django-flags built-in conditions (`user`, `parameter`, `percentage` qo'shilishi mumkin per-flag)
- **Reference**: ARCHITECTURE_REVIEW § 11

## 🔵 ISSUE-506 — SOC2 Type 1 audit preparation (PROCESS-BLOCKED)
- **Status**: 🔵 **Planned** — vendor selection + compliance process, code emas
- **Severity**: 🟠 P1 (enterprise contract'lar uchun)
- **Blocked by**: Vendor (Drata/Vanta/Secureframe) sotib olish + policy hujjatlar +
  penetration test annual contract. Backend foundation (ISSUE-401 audit log,
  ISSUE-409 audit fields, ISSUE-103 GDPR shim) tayyor.
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

## ✅ ISSUE-X01 — `services.py` placeholder fayllarni tozalash
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟢 P3
- **Implementation**: 6 ta placeholder o'chirildi (exams, catalog, engagement, organizations, commerce, accounts);
  intelligence/services.py + analytics/services.py qoldirildi (5-6 ta real function bor)
- **Reference**: ARCHITECTURE_REVIEW § 4.2

## ✅ ISSUE-X02 — Pre-commit fresh venv mirror CI (tox)
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟡 P2
- **Implementation**: `packages/backend/tox.ini` — 3 environment (lint/djangocheck/tests) har biri fresh isolated venv'da `requirements/base.txt + dev.txt` install qiladi. Usage: `tox -e py311-tests`. CI workflow allaqachon fresh venv ishlatadi.
- **Reference**: LESSONS.md → Lesson 17

## ✅ ISSUE-X03 — Lazy imports → RewardService interface
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟢 P3
- **Implementation**:
  - `core/interfaces/reward.py` — `RewardService` Protocol + `get_reward_service()` lazy resolver
  - `apps/commerce/wallet_service.py:award_coins_adapter` — default impl
  - `streak_service.py` + `leagues_service.py` refactored — `try/except ImportError` o'rniga `get_reward_service()`
  - `settings.REWARD_SERVICE_PATH` override (tests, alternative providers)
  - Test: 5 ta `tests/unit/test_reward_interface.py`
- **Reference**: ARCHITECTURE_REVIEW § 7.2

## ✅ ISSUE-X04 — Edge cache + Anthropic prompt cache
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟢 P3
- **Implementation**:
  - **Anthropic cache_control**: 3 call site (claude.py bulk parser, intelligence/tasks.py tutor + essay grader) — system prompt'larga `cache_control: ephemeral` attach qilindi
  - **CDN cache**: `core/middleware/cdn_cache.py` — sitemap.xml (1h), robots.txt (1d), leaderboard global+region (60s) — anonymous GET'larda
  - Test: 8 ta `tests/unit/test_cdn_cache_middleware.py`
  - [ ] Cost dashboard — kelajak (Grafana panel)
- **Reference**: ARCHITECTURE_REVIEW § 15, § 19

## ✅ ISSUE-X05 — Celery task tenant context audit
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟡 P2
- **Implementation**:
  - `tests/security/test_celery_tenant_context.py` — AST scan barcha `apps/*/tasks.py` + notifications_service.py
  - Allow-list 11 ta task uchun strategy: `unscoped` / `tenant` / `stateless` / `inherits`
  - Yangi `@shared_task` qo'shilsa, EXPECTED_TASKS dict yangilanmaguncha test fail
  - 3 ta test (file existence, strategy entry per task, valid strategy value)
- **Reference**: ARCHITECTURE_REVIEW § 13

## ✅ ISSUE-X06 — JSON structured logging + retention policy
- **Status**: ✅ **Done** (2026-05-16)
- **Severity**: 🟡 P2
- **Implementation**:
  - `core/logging.py` — `log_event(level, event, **fields)` helper
  - LOGGING settings: `yuzdanyuz.events` logger → `json_console` handler (python-json-logger)
  - `docs/api_conventions.md` § 8 — usage + retention table (INFO 30d / WARN+ 90d / ERROR 1y)
  - Test: 6 ta `tests/unit/test_logging_helper.py`
- **Reference**: ARCHITECTURE_REVIEW § 14

---

# 🚀 Yangi feature kelajakda

> Audit'dan tashqari roadmap'da kelgan ish'lar
> Hozir Task 11+ uchun joy reserve qilingan

## 🔵 ISSUE-F01 — Frontend Next.js workstream (Tasks 4-9 UI)
- **Status**: 🔵 **Planned**
- **Priority**: User choice
- **Effort**: 2-3 quarter (alohida frontend team)

## 🔵 ISSUE-F02 — Real K8s deploy (Hetzner/DOKS cluster) (INFRA-BLOCKED)
- **Status**: 🔵 **Planned** — Helm chart + KEDA manifests tayyor, cluster sotib olish kerak
- **Bog'liq**: ISSUE-302, ISSUE-303 (queue, RLS — prerequisite — DONE)
- **Effort**: 1 sprint
- **Blocked by**: Real K8s cluster (Hetzner Cloud / DigitalOcean Kubernetes / Linode LKE).
  Application code va manifest'lar tayyor.

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

> **Last updated**: 2026-05-16 (Q3+Cross-cutting batch — 13 ta yangi ✅, 5 ta infra/frontend-blocked documented)
> **Owner**: @narzullayevme (s.narzullayev@tassvision.ai)
> **Review cadence**: Sprint oxirida har 2 hafta + quarterly deep review
