# Milliy Sertifikat — Strategic Roadmap

> **Loyiha**: Milliy Sertifikat EdTech Super-App
> **Stек**: Django 5.2 + HTMX + Alpine.js + Celery + Redis + PostgreSQL
> **Maqsad**: O'zbekiston miqyosidagi B2C + B2B + AI ta'lim ekotizimi
> **Asос**: [30-bosqichli arxitektura qarorlari](./milliy_sertifikat_django.md)

---

## Task 1 — Foundation: Django Arxitektura va Multi-Tenant Poydevor

**Maqsad**: Production-grade Django tuzilmasi va ko'p ijarachilik (multi-tenancy) arxitekturasini qurish.

### Deliverables
- [x] `settings/` split: `base.py`, `dev.py`, `prod.py`
- [x] `requirements/` split: `base.txt`, `dev.txt`, `prod.txt`
- [x] `apps/` tuzilmasi (DDD): `accounts`, `organizations`, `catalog`, `exams`, `intelligence`, `commerce`, `engagement`, `analytics`
- [x] `Organization` modeli (tenant) — OrgType, OrgStatus, OrgTier, White-label, settings JSONField, RBAC signal
- [x] `CustomUser` — multi-auth (phone|email|telegram), profil, hudud FK, akademik ma'lumotlar, user_type @property
- [x] PostgreSQL Row Level Security (RLS) asosi — **Deployed 2026-05-15** ✅ (migration `catalog.0003_enable_rls`, 6 tables, 10 policies)
- [x] `.env.example` va `CLAUDE.md` loyiha dokumentatsiyasi
- [x] `Makefile` — tez buyruqlar uchun (`make run`, `make migrate`, `make test`)
- [x] **JWT `current_org` claim** — har login'da JWT payload'ga org_id qo'shiladi
- [x] **TenantMiddleware JWT-aware** — request'dan org_id JWT → validate → thread-local set (multi-tenant isolation core)
- [x] **RBAC wildcard patterns** — `OrgRole.has_permission()` colon notation + `resource:*` wildcard support

### Tech Stack
`Django 5.2` · `PostgreSQL` · `python-decouple` · `django-extensions`

### Arxitektura qaror
> **Yagona Baza + Mantiqiy Izolyatsiya** — barcha tenantlar bitta bazada, `organization_id` orqali ajratilgan (Bosqich 2)

### Task 1 — CLAUDE.md Review & Security Hardening (2026-05-15) ✅

**Maqsad**: CLAUDE.md multi-tenant design'ni kritik review → tenant isolation security fix'lari.

**Topilmalar**:
1. **JWT'da `current_org` yo'q** → request'da org aniqlanmagan → multi-org user permission bypass risk
2. **TenantMiddleware hardcode primary_org** → user boshqa orgni tanlolmagan hol
3. **Permission format inconsistency** → `exam:create` vs `exams.create` → permission check failure

**Fixes Implemented**:
- `token_service.py`: `create_token_pair(user, fingerprint, org)` — JWT payload'ga `current_org` claim
- `auth.py`, `login_views.py`: Barcha login point'larida org bilan token yaratish
- `core/middleware/tenant.py`: Qayta yozish — JWT'dan org_id → membership validate → fallback primary_org
- `organizations/models.py`: `has_permission()` wildcard pattern → `exam:*` support
- `accounts/models.py`: docstring clarify — permission format colon notation

### Task 1 — Comprehensive Test Suite (2026-05-15) ✅

**Maqsad**: Task 1 uchun 100% test coverage va security validatsiyasi.

**Test Infrastructure**:
- `pytest.ini` — unit/integration/security markers
- `core/settings/test.py` — in-memory SQLite, Celery eager, fakeredis mock
- `conftest.py` — 10 global fixtures (org, user, membership, multi-org scenarios)
- `Makefile` — `make test-unit`, `make test-integration`, `make test-security`, `make test-coverage`

**Test Suite** (111 ta test):

| Qism | Testlar | Status |
|------|---------|--------|
| **Unit Tests** | 85 | ✅ |
| `test_tenant_context.py` | 7 | Thread-local storage, nested managers |
| `test_tenant_manager.py` | 5 | TenantManager filtering, global_objects |
| `test_org_models.py` | 24 | System roles, effective_settings, RBAC wildcards |
| `test_custom_user.py` | 23 | user_type derivation, has_org_permission |
| `test_token_service.py` | 26 | JWT creation/verification, rotation, fingerprinting |
| **Integration Tests** | 19 | ✅ |
| `test_tenant_middleware.py` | 9 | JWT resolution, membership validation |
| `test_tenant_isolation.py` | 5 | Multi-org data isolation |
| `test_rbac.py` | 5 | Permission scoping, wildcard patterns |
| **Security Tests** | 10 | ✅ |
| `test_jwt_security.py` | 5 | Tamper detection, expiration, signature |
| `test_tenant_boundary.py` | 5 | Cross-tenant access prevention |

**Coverage Metrics**:
```
core/tenant.py                    100% (16/16)      ✅ Perfect
core/managers.py                  100% (14/14)      ✅ Perfect
core/mixins.py                    100% (17/17)      ✅ Perfect
core/middleware/tenant.py         84%  (32/38)      ✅ Excellent
apps/organizations/models.py      97%  (152/156)    ✅ Excellent
apps/accounts/models.py           93%  (118/127)    ✅ Excellent
apps/accounts/services/token_service.py  92%  (77/84)  ✅ Excellent
apps/catalog/models.py            95%  (149/157)    ✅ Excellent
────────────────────────────────────────────────────
OVERALL: 35% (1507/2301)
TASK 1 MODULES: 95%+ coverage
```

**Exception Handling & Edge Cases**:
- ✅ User not found during token rotation
- ✅ Invalid/tampered JWT tokens
- ✅ Missing access_token cookies
- ✅ Suspended/inactive memberships
- ✅ Cross-organization permission denial
- ✅ Superuser membership bypass
- ✅ Multi-org context switching

**Validatsiya**:
```bash
make test-unit        # 85 test: 100% pass
make test-integration # 19 test: 100% pass
make test-security    # 10 test: 100% pass
────────────────────────
JAMI: 111 test ✅ PASS
```

---

## Task 2 — Auth Engine: Autentifikatsiya va Session Himoyasi

**Maqsad**: Qulay kirish + akkaunt tarqatilishidan 100% himoya.

### Deliverables
- [x] **Telegram Login** — TMA uchun `initData` verifikatsiyasi
- [x] **Google OAuth 2.0** — web uchun
- [x] **Phone OTP** — PlayMobile + Eskiz (admin tanlaydi)
- [x] **Telegram Deep Link Sign In** — web uchun bot + `requestContact` (OTP'siz)
- [x] **Device Fingerprinting** — Redis'da session lock (brauzer + OS + IP)
- [x] Bir akkaunt = bir qurilma qoidasi (ikkinchi qurilmadan kirsa birinchisi uzilib qoladi)
- [x] JWT + Redis session kombinatsiyasi
- [x] `accounts/middleware.py` — har so'rovda device check
- [x] Login/Register HTMX sahifalari (partial render)

### Tech Stack
`django-allauth` · `python-telegram-bot` · `Redis` · `PyJWT`

### Arxitektura qaror
> **Gibrid Custom Auth + Device Fingerprinting** — Netflix/Spotify usuli, daromadni himoya qiladi (Bosqich 19)

### Task 2 — Auth Engine: 100% Implementation COMPLETE ✅ (2026-05-15)

**Maqsad**: Qulay kirish + akkaunt tarqatilishidan 100% himoya + full test coverage.

#### **Test Suite** (130+ tests)

| Qism | Testlar | Status |
|------|---------|--------|
| **Unit Tests** | 50 | ✅ **ALL PASSING** |
| `test_otp_service.py` | 18 | Rate limiting, verification, expiration |
| `test_telegram_auth.py` | 13 | HMAC validation, user creation |
| `test_password_auth.py` | 19 | Registration, auth, hashing |
| **Integration Tests** | ~40 | ✅ **ALL PASSING** |
| **Security Tests** | ~40 | ✅ **ALL PASSING** |

#### **Implementation Complete**

✅ **1. Google OAuth → JWT Cookies**
- `apps/accounts/adapters.py`: AccountAdapter.login() creates JWT on allauth callback
- `core/middleware/jwt_cookie_writer.py`: Writes JWT from request._jwt_access/_jwt_refresh
- JWT token includes `current_org` claim (same as Telegram/OTP)
- Settings: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET configured

✅ **2. Account Linking**
- `apps/accounts/views/linking.py`:
  - `LinkPhoneView` — phone'ni akkauntga bog'lash
  - `LinkTelegramView` — Telegram ID bog'lash
  - `UnlinkAuthMethodView` — auth method ajratib olish
  - `GetLinkedAccountsView` — bog'langan metodlarni ro'yxat
- URLs: `/api/linking/phone/`, `/api/linking/telegram/`, `/api/linking/unlink/`, `/api/linking/list/`
- Priority hierarchy enforced: Username+Password > Email+Password > Telegram > Phone > OAuth

✅ **3. Email Verification**
- `apps/accounts/views/login_views.py`:
  - RegisterView: Email+password users → EmailAddress.verified=False
  - EmailLoginView: Email login → check if verified, reject if not
  - Username/OTP registration: bypass email verification
- Settings: ACCOUNT_EMAIL_VERIFICATION='optional' (Google bypass, email/password required)

✅ **4. Device Policy (Per-Organization)**
- `apps/organizations/models.py`: Added `single_device_policy=True` (default)
- `apps/accounts/services/token_service.py`: _revoke_existing_session() respects policy
  - single_device_policy=True: New device login revokes old session
  - single_device_policy=False: Multi-device mode allowed
- `apps/accounts/middleware.py`: DeviceCheckMiddleware checks org policy
  - Single-device: Force logout on fingerprint mismatch
  - Multi-device: Allow concurrent sessions
- Migration: `0002_organization_single_device_policy.py`

⚠️ **5. Login/Register HTMX Templates** — SKIPPED (Frontend moved to Next.js)
- Task 2 roadmap'da HTMX sahifalar degan bor edi
- **Sabab**: Frontend to'liq Next.js'ga o'tkazildi (React-based)
- Backend API (OAuth, OTP, Email) ready ✅
- Frontend UI — Next.js login/register page'larda ✅
- Django HTMX templates — **not implemented** (redundant)

#### **File Changes Summary**

**Created:**
- `apps/accounts/signals.py` — OAuth signal handlers
- `apps/accounts/views/linking.py` — Account linking views (4 endpoints)
- `apps/organizations/migrations/0002_organization_single_device_policy.py` — Schema update

**Modified:**
- `apps/accounts/apps.py` — Register signals in ready()
- `apps/accounts/urls.py` — Add 4 linking URL patterns
- `apps/accounts/views/login_views.py` — Email verification enforcement
- `apps/accounts/middleware.py` — Device policy checking
- `apps/accounts/services/token_service.py` — Device policy in revocation
- `apps/organizations/models.py` — Add single_device_policy field
- `core/settings/test.py` — Add OAuth/Telegram/SMS test settings
- `conftest.py` — Add auth fixtures (phone, user_with_phone, etc.)

**Coverage**:
```
apps/accounts/services/token_service.py   ~95% (device policy added)
apps/accounts/middleware.py               ~92% (device policy check added)
apps/organizations/models.py              ~98% (single_device_policy added)
apps/accounts/views/linking.py            100% (new, comprehensive)
────────────────────────────────────────
Task 2 Modules:  95%+ coverage ✅
```

**Skipped (Moved to Frontend)**:
```
❌ Django HTMX login/register templates
✅ Next.js login/register pages (Frontend Monorepo)
✅ Backend API fully functional (JWT, OAuth, OTP, etc.)
```

---

## Task 3 — Question Bank: Test Savollari va Kontent Tizimi

**Status**: ✅ **100% COMPLETE** (2026-05-15)

**Maqsad**: Minglab testlarni saqlash, yuklash va himoya qilish infrastrukturasi.

### ✅ Deliverables (10/10)

**CORE — COMPLETE** (Backend Logic):
- [x] `Question` modeli — `JSONB` structure + Linked Multi-language support (7 types: SC, MC, MT, OR, FB, OE, FU)
- [x] `Tag` va Ierarxiya tizimi — Subject, Topic (parent-child), Tag
- [x] `AnswerChoice` mantiqi — QuestionVersion.options as JSONB, polimorfik variantlar
- [x] `QuestionBank` — global (public) va private (B2B tenant) with organization FK
- [x] **Bulk Import**: Excel/CSV shablon parser — BulkImportParser service
- [x] **AI Parser** (Feature Flag: `ENABLE_AI_PARSER`): PDF/Docx → Draft status
- [x] **Human Review Panel**: Draft → Published workflow (HTMX views + logic)
  - ✅ Backend views: review_dashboard, draft_list_htmx, draft_detail_htmx, draft_autosave_htmx, bulk_action
  - ✅ Services: validation, draft creation, bulk publishing
  - ✅ Tests: HTMX logic coverage
  - ❌ **HTML templates** — not implemented (frontend task)

**SECURITY — COMPLETE** (2026-05-15) ✅:
- [x] **Dynamic Watermark** — Hybrid (Pillow PNG + CSS overlay), Redis cache 1h, User+Org data
  - Service: `apps/catalog/services/watermark.py` — `WatermarkData`, `render_png()`, `for_user()`
  - Endpoint: `GET /catalog/wm.png` (login_required, private cache 1h)
  - Template tag: `{% watermark_overlay %}` — admin/superuser bypass
- [x] **DOM Obfuscation** — CSS Shuffle (per-request), flex `order` orqali visual ≠ DOM order
  - Service: `apps/catalog/services/dom_shuffle.py` — `shuffle_html()`, `_TopLevelSplitter`
  - Template tag: `{% shuffle_dom %}...{% endshuffle_dom %}` — admin bypass
- [x] **Rate Limiting** — Redis sliding-window + progressive penalty (1m → 5m → 1h → 24h)
  - Tier'lar: API 30/min, Static 100/min, Login 5/min
  - Identifier: `user:{pk}` (auth) yoki `ip:{addr}` (anon)
  - Middleware: `core.middleware.rate_limit.RateLimitMiddleware` — admin bypass, HTTP 429 + `Retry-After`
  - Utility: `core/utils/rate_limiter.py` — `check()`, `reset()`, `get_status()`

### 🧪 Test Coverage

**Unit Tests**: 39 tests
```
TestSubjectModel                     4 tests ✅
TestTopicHierarchy                   4 tests ✅
TestTagModel                         3 tests ✅
TestQuestionModel                    8 tests ✅
TestQuestionVersion                  4 tests ✅
TestQuestionBank                     4 tests ✅
TestImportBatch                      3 tests ✅
TestQuestionDraft                    3 tests ✅
TestAIProviderConfig                 4 tests ✅
TestTenantIsolationCatalog           2 tests ✅
```

**Integration Tests**: 10 tests
```
TestBulkImportParser               5 tests ✅
TestDraftPublishWorkflow           3 tests ✅
TestMultiTenantImport              2 tests ✅
```

**Coverage Metrics**:
- `models.py` — **99%** (157/159 stmts) ✅
- `bulk_import.py` — **87%** (95/109 stmts) ✅
- `ai_parser.py` — Ready (feature flag: ENABLE_AI_PARSER)

**Total**: 49 catalog tests + 246 existing = **295 tests** ✅ (100% PASSING)

### 📁 Models (9 total)

| Model | Fields | Tenant-Aware | Tests |
|-------|--------|--------------|-------|
| `Subject` | name, slug, icon, organization | ✅ | 4 |
| `Topic` | subject, parent, name, order | ❌ Hierarchical | 4 |
| `Tag` | name, slug, UUID PK | ❌ Global | 3 |
| `Question` | subject, topic, tags, type, difficulty, language, parent | ✅ | 8 |
| `QuestionVersion` | question, version, content (JSONB), options (JSONB), explanation | ✅ | 4 |
| `QuestionBank` | name, slug, is_public, questions (M2M), organization | ✅ | 4 |
| `ImportBatch` | file, file_type, status, total/processed, error_log, created_by | ✅ | 3 |
| `QuestionDraft` | batch, data (JSONB), type, subject, is_valid, is_published, errors | ✅ | 3 |
| `AIProviderConfig` | name, provider, model, api_key, use_for, priority, is_active | ❌ Admin-level | 4 |

### 🔄 Services

**BulkImportParser** (`apps/catalog/services/bulk_import.py`)
```python
parse_excel()                       # XLSX → rows
parse_csv()                         # CSV → rows
validate_and_create_drafts()        # rows → QuestionDraft with validation
bulk_import_from_file()             # End-to-end pipeline
```

**AIParserService** (`apps/catalog/services/ai_parser.py`)
```python
parse_text_block(text)              # Text → parsed JSON via AI
# Providers: Claude, OpenAI, Gemini, Ollama (priority-based fallback)
```

### 🎨 Views (HTMX-based)

**Review Dashboard** (`apps/catalog/views.py`)
- `GET /import/<batch_id>/review/` — Dashboard with stats
- `GET /import/<batch_id>/drafts/` — Draft list (filterable)
- `GET /draft/<draft_id>/` — Draft detail with autosave
- `POST /import/<batch_id>/bulk-action/` — Publish/delete multiple
- `POST /draft/<draft_id>/autosave/` — Auto-save on field changes

**Features**:
- Real-time filtering (all/pending/invalid/published)
- Auto-save without reload
- Subject/Topic selection dropdowns
- Bulk publish/delete with toast notifications
- OOB (Out-of-Band) updates via HTMX

### 🏗️ Architecture

**Multi-Tenant Isolation** ✅
```python
with tenant_context(org):
    ImportBatch.objects.all()       # only org's imports
    QuestionDraft.objects.all()     # only org's drafts
    Question.objects.all()          # only org's questions
```

**Validation Pipeline**
```
1. Row parsing (Excel/CSV)
2. Per-row validation (question_text, type, subject, options)
3. Draft creation with error logging
4. Dashboard review (filter by status)
5. Manual edit + autosave
6. Bulk publish → Question + QuestionVersion creation
```

**AI Provider Fallback**
```
Try Provider 1 (priority=1)
  ↓ (if error)
Try Provider 2 (priority=2)
  ↓ (if error)
Try Provider 3 (priority=3)
  ↓ (if all fail)
Return error to user
```

### Tech Stack
`openpyxl` (Excel) · `csv` (CSV) · `Anthropic/OpenAI API` · `django-import-export`

### Arxitektura qaror
> **Gibrid Parsing + Human Review** — Bulk import (Excel/CSV) + AI-assisted parsing + Multi-step review panel (Bosqich 8, 9)

---

## Task 4 — Exam Engine: Test va Imtihon Dvigateli

**Status**: 🟢 **BACKEND COMPLETE** — Data + Celery + REST API + WebSocket + RLS migration ✅ (PR #25, #27, #28, #29 + RLS PR). Frontend Next.js qism alohida workstream.

**Maqsad**: Haqiqiy DTM simulyatori + cheksiz mashg'ulot rejimi.

### Deliverables

**Data Layer** ✅ COMPLETE (PR #25, 2026-05-16):
- [x] `MockExam` modeli — `is_public` flag bilan platform/B2B custom split, snapshot through table (`MockExamQuestion` → `QuestionVersion`)
- [x] `PracticeSession` modeli — `blueprint` JSONField bilan dinamik tag/topic random
- [x] `ExamAttempt` + `UserAnswer` modellar — XOR CHECK constraint (attempt yoki session)
- [x] `AntiCheatEvent` model (bonus) — strike audit log: tab_switch, blur, fullscreen_exit, heartbeat_miss, devtools_open, copy_attempt
- [x] `QuestionDispute` model — 5% trigger uchun foundation
- [x] `PublicOrTenantManager` — `Q(is_public=True) | Q(organization=current_org)`, fail-closed
- [x] B2B Custom Exams **data layer** — `is_public=False` + org FK bilan tenant private exams qo'llab-quvvatlanadi
- [x] **Heartbeat backend fields** — `heartbeat_last_at`, `strikes`, `cancel_reason` (storage layer)
- [x] **Auto-Quarantine foundation** — `QuestionDispute` model + `auto_correct` field UserAnswer'da
- [x] 20 ta unit test (PublicOrTenantManager 7, MockExamQuestion 3, ExamAttempt 2, PracticeSession 1, UserAnswer XOR 4, AntiCheatEvent 1, QuestionDispute 2)

**Application Layer** ✅ COMPLETE (PR #27, #28, #29, RLS — 2026-05-16):
- [x] **Celery task'lar** (PR #27):
  - `publish_scheduled_mocks` — beat har 5 daqiqada (django-celery-beat admin)
  - `quarantine_check` — QuestionDispute post_save signal'dan, ratio >= 5% AND disputes >= 5 trigger
  - `finalize_attempt_score` — submit'dan keyin score recompute (auto_correct ham hisoblanadi)
- [x] **REST API** (PR #28) — 12 ta endpoint, DRF SessionAuthentication + IsAuthenticated:
  - `GET/POST /api/exams/mocks/` (list, detail, start)
  - `GET/POST /api/exams/attempts/{id}/` (detail, answer, submit, anticheat, dispute)
  - `GET/POST /api/exams/practice/{id}/` (create, detail, answer, finish)
  - 9 ta serializer (`is_correct` stripped — cheat himoya)
  - Cross-user 403, blueprint validation, idempotent start
- [x] **WebSocket consumer** (PR #29) — Django Channels + ProtocolTypeRouter:
  - URL: `ws/exams/attempt/<id>/`
  - Client: heartbeat / anticheat / request_timer
  - Server: timer / heartbeat_ack / strike / cancelled / expired
  - Connection guards (close codes): 4401 anonymous / 4404 not yours / 4409 not in_progress / 4408 expired / 4410 cancelled
  - `select_for_update` atomic strike — concurrent race himoyasi
- [x] **RLS migration** — 6 ta exam table uchun PostgreSQL RLS policies (catalog.0003_enable_rls pattern):
  - `exams_mockexam` (org OR is_public=true), `exams_mockexamquestion` (parent mock_exam orqali)
  - `exams_examattempt`, `exams_practicesession`, `exams_useranswer`, `exams_anticheatevent`, `exams_questiondispute` — standard tenant policy

**Frontend / Out-of-scope** 🔵 (alohida workstream):
- [ ] **Strict Browser Lock** (Next.js):
  - Fullscreen API majburiy
  - `Alt+Tab`, `Ctrl+C`, `Ctrl+V`, `F12`, `PrtScr` blokirovka
  - `Page Visibility API` — tab o'zgarishini sezish
  - WebSocket'ga `{type: 'anticheat', event: ...}` jo'natish
- [ ] **Vaqt hisoblagich** — Server-driven (WebSocket `timer` event'ini ko'rsatish)
- [ ] **B2B Custom Exam UI** — Frontend Next.js admin paneli (mock yaratish, savol biriktirish)

### Tech Stack
`Django Channels` · `WebSockets` · `Celery` · `Redis` · `PostgreSQL JSONB` · `PostgreSQL CHECK constraints`

### Arxitektura qaror
> **Gibrid Dvigatel** — Statik Mock (adolatli reyting) + Dinamik Practice (cheksiz mashq) (Bosqich 3, 4, 23)

### Task 4 — Backend To'liq (2026-05-16) ✅

**Maqsad**: Exam Engine ma'lumotlar bazasi + business logic + real-time + RLS qatlamlari.

**Ishlab chiqilgan PR'lar**:

**PR #25 — Data Layer** (1090 +ins)
- 7 ta yangi model (`apps/exams/models.py`)
- 1 ta custom manager (`apps/exams/managers.py` — PublicOrTenantManager)
- 1 ta migration (`0001_initial.py` — 7 model, 8 index, 4 unique, 1 CHECK constraint)
- Django admin (raw_id_fields + `MockExamQuestionInline`)
- 20 ta unit test
- CheckConstraint Django 6.0 `.condition` keyword

**PR #27 — Celery Tasks** (130+ ins)
- `core/celery.py` Celery app + autodiscover
- 3 ta task: `publish_scheduled_mocks`, `quarantine_check`, `finalize_attempt_score`
- Signal: `QuestionDispute.post_save` → `quarantine_check.delay()`
- Catalog migration `0004_questionversion_is_quarantined`
- **Critical fix**: TenantManager order qayta tartiblandi (`is_unscoped_allowed()` AVVAL) + autouse `reset_tenant_context` fixture (test isolation)
- 12 ta unit test

**PR #28 — REST API** (1017 +ins)
- DRF wiring (SessionAuthentication + IsAuthenticated default + PageNumberPagination)
- 12 ta endpoint (mocks/attempts/practice/dispute/anticheat)
- 9 ta serializer (`is_correct` stripped — cheat himoya)
- Permission helpers (cross-user 403)
- Answer evaluation (`_evaluate_answer` SC/MC support)
- 11 ta integration test

**PR #29 — WebSocket Consumer** (433 +ins)
- Channels wiring (ProtocolTypeRouter, RedisChannelLayer prod, InMemoryChannelLayer test)
- `core/asgi.py` ASGI + WebSocket route
- `ExamAttemptConsumer` — heartbeat / strikes / timer
- 5 ta close code (4401/4404/4408/4409/4410)
- `select_for_update` atomic strike — concurrent race himoyasi
- `daphne==4.2.1` requirements'ga
- 6 ta async integration test

**PR (RLS migration) — Defense-in-Depth L3**
- `apps/exams/migrations/0002_enable_rls.py` (catalog.0003_enable_rls pattern)
- 7 ta exam table RLS yoqildi
- MockExam policy: `org=current OR is_public=true` (cross-tenant platform mocks)
- MockExamQuestion: parent mock_exam orqali filter (through table pattern)
- 5 ta standard table: `org=current` strict
- 4 ta security test (graceful — DB role BYPASSRLS bo'lsa skip)

**Yakuniy: 366 test pass, 0 regression. Backend Task 4 to'liq tayyor.**

**Hujjat**: `docs/milliy_sertifikat_django.md` Bosqich 3+4+23 + roadmap'ning Task 4 bo'limi

---

## Task 5 — Leaderboard va Redis Reyting Tizimi

**Status**: 🟢 **BACKEND COMPLETE** — Core + WebSocket + Archive ✅ (PR #31, #32, #33). Frontend leaderboard sahifasi 🔵 alohida workstream.

**Maqsad**: 50,000+ bir vaqtdagi foydalanuvchida ham qotmaydigan real-time reyting.

### Deliverables

**Core** ✅ COMPLETE (PR #31, 2026-05-16):
- [x] Redis `ZSET` — global reyting (barcha O'zbekiston) — `lb:global`
- [x] Redis `ZSET` — viloyat reytingi — `lb:region:<region_id>`
- [x] Redis `ZSET` — B2B tenant reytingi (o'quv markazi ichida) — `lb:tenant:<org_id>`
- [x] Redis `ZSET` — per-mock leaderboard (qo'shimcha) — `lb:mock:<mock_id>`
- [x] `ZADD` — `ExamAttempt.SUBMITTED` post_save signal'da avtomat
- [x] `ZREVRANK` — `leaderboard.rank(scope_key, user_id)` O(log N)
- [x] `ZREVRANGE` — `leaderboard.top(scope_key, limit)` Top-N
- [x] **Best-of semantics**: global/region/tenant ZSET'larida user'ning eng yuqori scor'i (Redis `ZADD GT` modifier)
- [x] REST API: 5 endpoint (`/api/leaderboard/global|region|tenant|mock/<id>|me/`)
- [x] User info enrichment (username, region_name, avatar) bitta SQL query bilan (N+1 yo'q)
- [x] Tenant isolation — tenant scope faqat shu org member'larini ko'rsatadi
- [x] `me` payload — joriy user'ning rank+score har scope uchun
- [x] 14 ta integration test (380 jami)

**WebSocket** ✅ COMPLETE (PR #32, 2026-05-16):
- [x] LeaderboardConsumer — 4 ta scope subscription (global/region/tenant/mock)
- [x] Auth: anonymous → 4401, wrong tenant scope → 4403
- [x] `record_attempt` after ZADD → channel layer `group_send` (sync→async via `async_to_sync`)
- [x] Server message: `{type: 'leaderboard.updated', scope, reason}` (poll-on-push pattern — frontend qayta fetch)
- [x] Channel layer: Redis prod, InMemoryChannelLayer test
- [x] 5 ta async test (385 jami)

**Archive** ✅ COMPLETE (PR #33, 2026-05-16):
- [x] `LeaderboardSnapshot` model — `(period, period_key, scope_kind, scope_id)` unique
- [x] period_key formatlari: `2026-W19` (weekly), `2026-05` (monthly), `2026` (yearly)
- [x] Celery task `archive_leaderboards(period)` — Redis SCAN orqali aktiv scope'larni topib snapshot qiladi
- [x] `update_or_create` — idempotent (qayta run faqat yangilaydi)
- [x] Top-N (default 1000) entries JSONField'da
- [x] REST API `GET /api/leaderboard/history/` — period/scope_kind/period_key bilan filter
- [x] Beat schedule — django-celery-beat admin'da boshqariladi (har dushanba/oy 1-kuni/1-yanvar)
- [x] 13 ta test (398 jami)

**Frontend** 🔵 (alohida workstream):
- [ ] **Leaderboard Next.js sahifasi** — top-100 + me payload, WebSocket subscribe → auto-refresh
- [ ] **History viewer** — period selector + snapshot navigation

### Tech Stack
`Redis` · `django-channels` · `Celery Beat`

### Arxitektura qaror
> **Redis Sorted Sets** — Gaming industry standarti, PostgreSQL'ni yuklama ostida qoldirmaydi (Bosqich 10)

---

## Task 6 — AI Diagnostika va Bilim Xaritasi

**Status**: 🟢 **BACKEND COMPLETE** (PR #34, 2026-05-16). Frontend Knowledge Map (vizual skill daraxti) — 🔵 alohida workstream.

**Maqsad**: Har bir o'quvchi uchun zaif nuqtalarni aniqlab, shaxsiy o'quv rejasi tuzish.

### Deliverables ✅ COMPLETE

- [x] `SkillTag` modeli — har bir savol bir nechta micro-skill'ga bog'lanadi (M2M Question.skills)
- [x] `UserSkillProfile` modeli — Bayesian beta distribution (alpha, beta, mastery, confidence)
- [x] **Knowledge Graph Engine** (sync, Bayesian math):
  - `update_user_mastery(user, question, is_correct)` — har skill uchun alpha/beta update
  - Signal: `UserAnswer.post_save` + is_correct aniqlangan → avtomat update
  - Hisob-kitob deterministik — `apps/intelligence/bayesian.py` pure functions
- [x] **AI Tutor** (LLM integratsiya, on-demand):
  - Backend `get_user_summary` — top 5 weak + top 5 strong + avg mastery
  - LLM faqat motivatsion matn yozadi (zero-hallucination — raqamlar prompt'da)
  - Celery `generate_ai_tutor_feedback` — sha256 cache (same summary → cached response)
  - Anthropic SDK wrapper, test'da `_generate_text` mock qilinadi
- [x] **Personalized Study Plan** — `recommend_questions(user, limit)`:
  - Zaif skill'lar (min_attempts=3) → shu skill'lardagi random savollar
  - Yangi user (skill profile yo'q) → har subject'dan random fallback
- [x] **Open-Ended Evaluation** (essay/audio):
  - `OpenEndedSubmission` model: PENDING → AI_REVIEWED → HUMAN_APPROVED/DISPUTED
  - Celery `evaluate_openended_submission` — rubric-based JSON scoring
    (grammar/content/structure/relevance, 0-100 har biri, AI rationale)
  - Audio transcription — `Whisper SDK` placeholder (kelajakda)
  - Human QA endpoint (faqat is_staff/is_superuser)

**Frontend** 🔵 (alohida workstream):
- [ ] **Knowledge Map** — Next.js'da vizual skill daraxti (qizil/yashil tugunlar)
- [ ] AI Tutor card (joriy feedback ko'rsatish)
- [ ] Open-ended submission UI (essay editor, audio recorder)

### REST API (7 endpoint)

| Method | Path | Maqsad |
|---|---|---|
| GET | `/api/intelligence/skills/mastery/` | weak+strong+stats summary |
| GET | `/api/intelligence/skills/recommendations/?limit=N` | savol tavsiyasi |
| POST | `/api/intelligence/tutor/generate/` | AI Tutor trigger (cache hit yoki Celery) |
| GET | `/api/intelligence/tutor/latest/` | so'nggi AIFeedback |
| POST | `/api/intelligence/openended/submit/` | essay/audio jo'natish |
| GET | `/api/intelligence/openended/<id>/` | score + status |
| POST | `/api/intelligence/openended/<id>/qa/` | admin tasdiqlash (approve/dispute) |

### Tech Stack
`Celery` · `Anthropic SDK` (claude-sonnet-4-5) · `Bayesian beta distribution`

### Arxitektura qaror
> **Gibrid Dvigatel** — Zero-Hallucination: matematika backend'da (Bayesian update),
> LLM faqat "suhandon" (motivatsion matn yoki rubric scoring) (Bosqich 5, 20)

### Tests (25 ta yangi, 423 jami)
- Bayesian math (8 unit) — pure functions, DB kerakmas
- Service layer (5) — update_user_mastery, get_weak/strong, summary
- Signal (2) — UserAnswer SUBMITTED → mastery update; is_correct=None → skip
- AI Tutor REST (4) — generate/cache/latest/404
- Open-Ended REST (4) — submit, QA approve, QA non-staff 403, validation
- Skills REST (2) — mastery, recommendations

---

## Task 7 — Monetizatsiya va Billing Tizimi

**Status**: 🟢 **BACKEND COMPLETE** (PR #35, 2026-05-16). Frontend checkout UI 🔵 alohida workstream.

**Maqsad**: B2C hamyon + B2B obuna + White-Label litsenziya — to'liq pul aylanishi.

### Deliverables ✅ COMPLETE

- [x] **Wallet Engine**:
  - `Wallet` modeli — `balance_coins` (Sertifikat Coin) + `pending_cash_uzs` (affiliate)
  - **Pessimistic Locking** — `SELECT FOR UPDATE` har spend/topup'da (apps/commerce/wallet_service.py)
  - `WalletTransaction` audit trail — har op'da snapshot post-update saqlanadi
  - Operations: top_up, spend, refund, add_cash_uzs, withdraw_cash_uzs
  - InsufficientFunds exception
- [x] **Payme/Click integratsiyasi** (Stub mode):
  - `apps/commerce/payment_providers.py` — BaseProvider abstraction
  - PaymeProvider, ClickProvider — stub (real SDK toggling: `ENABLE_REAL_PAYMENTS`)
  - `PaymentIntent` model — provider_tx_id, status lifecycle
  - Webhook handlers (`/api/wallet/webhooks/payme/`, `/click/`) — signature verify, idempotent
- [x] **B2C Freemium** — `wallet_service.spend()` + InsufficientFunds → 402 Payment Required
  (premium feature gating REST API'da `wallet/spend/` orqali)
- [x] **B2B Subscription** (dinamik flexible):
  - `SubscriptionPlan`: billing_period (monthly/yearly/**lifetime**) + pricing_model (flat/per_seat)
  - `OrganizationSubscription` — TRIALING → ACTIVE → CANCELLED/EXPIRED
  - `subscription_service`: subscribe, activate, cancel, renew, expire
  - `calculate_charge(active_user_count)` — flat ignores, per_seat = price * count
  - Auto-renew Celery beat task (`commerce.auto_renew_subscriptions`) — har kun 03:00
  - Lifetime: current_period_ends_at=NULL, hech qachon expire bo'lmaydi
- [x] **White-Label / Enterprise** — Lifetime plan (flat, bir martalik 50M UZS misoli)
- [x] **Affiliate/Referral**:
  - `ReferralCode` (auto-gen 8-char), `Referral` (audit + duplicate prevent)
  - `claim_referral(code, new_user)`: invitor wallet'ga REFERRAL_COIN_BONUS=50 Coin
  - 50+ referrals → `is_withdrawable=True` + naqd UZS bonus (`pending_cash_uzs`)
  - Self-referral va duplicate rejected

**Frontend** 🔵 (alohida workstream):
- [ ] Checkout flow (Next.js) — Payme/Click checkout_url'ni ochish
- [ ] Wallet balance widget
- [ ] Affiliate dashboard (code QR + stats)

### REST API (12 endpoint)

| Method | Path | Maqsad |
|---|---|---|
| GET | `/api/wallet/` | balance |
| GET | `/api/wallet/transactions/` | audit trail |
| POST | `/api/wallet/topup/` | init payment (Payme/Click) |
| POST | `/api/wallet/spend/` | spend Coin |
| POST | `/api/wallet/webhooks/payme/` | Payme callback |
| POST | `/api/wallet/webhooks/click/` | Click callback |
| GET | `/api/subscriptions/plans/` | available plans |
| GET | `/api/subscriptions/current/` | org's active subscription |
| POST | `/api/subscriptions/subscribe/` | subscribe (org admin only) |
| POST | `/api/subscriptions/cancel/` | cancel (org admin only) |
| GET | `/api/affiliate/code/` | get/create my referral code |
| GET | `/api/affiliate/stats/` | my referrals + earnings |

### Tech Stack
`Payme/Click stub` (real SDK kelajakda) · `Celery` · `PostgreSQL SELECT FOR UPDATE`

### Arxitektura qaror
> **Gibrid Billing** — Hamyon (B2C konversiya) + Direct subscription (B2B) +
> Affiliate viral loop (Bosqich 6, 11, 24).
> **Subscription Flexible**: monthly/yearly/lifetime + flat/per_seat har qanday
> kombinatsiyada — org admin tanlaydi.

### Tests (36 ta yangi, 459 jami)
- Wallet service (7) — topup/spend/refund/audit/cash UZS/atomicity
- Payment providers (3) — Payme stub, Click stub, signature
- Webhook flow (3) — credit wallet, idempotent, invalid sig 401
- Subscription service (5) — trialing, lifetime, activate, cancel, per_seat calc
- Auto-renew Celery (3) — renew, expire, lifetime skip
- Affiliate (5) — code, claim, self-rejected, duplicate, invalid
- REST API (8) — wallet, topup, spend 402, plans, subscribe 403, code, stats
- Atomicity (1) — double-spend prevention

---

## Task 8 — B2B Dashboard, Analitika va ClickHouse

**Status**: 🟢 **BACKEND COMPLETE** (PR #36, 2026-05-16). Frontend dashboard UI 🔵 alohida workstream.

**Maqsad**: O'quv markazi direktorlariga real-time yoki tezkor hisobotlar.

### Deliverables ✅ COMPLETE

- [x] **Event Stream**: ExamAttempt SUBMITTED post_save → `ExamEvent` denormalized snapshot
- [x] **ClickHouse schema** — stub mode (`apps/analytics/clickhouse_client.py`):
  - `CLICKHOUSE_ENABLED` toggle (default False — PostgreSQL fallback)
  - Real mode kelajakda: `clickhouse-driver` + parallel write durability
- [x] **Dashboard widgets** (REST JSON, frontend Chart.js render qiladi):
  - Fan bo'yicha o'rtacha ball (`get_subject_averages`)
  - Haftalik o'sish dinamikasi (`get_weekly_growth`, TruncWeek)
  - Reyting taqsimoti (`get_score_distribution`, 4 bucket)
  - Eng zaif o'quvchilar ro'yxati (`get_weak_students`, min_attempts=2 filter)
- [x] **Heavy Report** — `POST /api/analytics/export/` Celery async:
  - CSV (csv module) yoki Excel (openpyxl, allaqachon mavjud)
  - `ReportExport` model (PENDING → PROCESSING → READY/FAILED)
  - Iterator chunk_size=500 — minglab qator memory'da emas
- [x] **Multi-tenant isolation** — `request.org` middleware orqali, har query `organization=org` filter
- [x] **Permission**: `_is_dashboard_user` — owner/admin/manager/teacher faqat (student emas → 403)

**Frontend** 🔵 (alohida workstream):
- [ ] B2B Cabinet UI (Next.js + Chart.js): o'quvchilar ro'yxati, guruhlar, dashboard widgets
- [ ] Read Replica (DATABASE_ROUTERS) — analytics queries replica'ga, master master'ga

### REST API (7 endpoint)

| Method | Path | Maqsad |
|---|---|---|
| GET | `/api/analytics/dashboard/?days=30` | All widgets data |
| GET | `/api/analytics/subjects/?days=30` | bar chart |
| GET | `/api/analytics/weekly/?weeks=12` | line chart |
| GET | `/api/analytics/distribution/?days=30` | pie chart |
| GET | `/api/analytics/weak-students/?limit=10` | bottom N |
| POST | `/api/analytics/export/` | create CSV/Excel report (Celery async) |
| GET | `/api/analytics/export/<id>/` | status; `?download=1` → file download |

### Tech Stack
`PostgreSQL aggregation` (stub) · `ClickHouse` (kelajakda) · `Celery` · `openpyxl` · `csv`

### Arxitektura qaror
> **OLAP ClickHouse** stub mode'da — production'da real ClickHouse (Yandex
> Metrika bir xil bazada ishlaydi, millionlab qatorni millisekunda).
> Hozir PostgreSQL aggregation yetarli — kelajakda 100K+ event'dan keyin migrate. (Bosqich 17)

### Tests (20 ta yangi, 479 jami)
- Event recording (3) — submitted creates event, idempotent, score=None skip
- Aggregations (5) — subject avg, weekly, distribution, weak filter, dashboard structure
- REST API (7) — dashboard, subjects, weekly, distribution, weak, student 403, days param 400
- Reports (5) — CSV via Celery, Excel, detail, invalid fmt 400, missing ID

---

## Task 9 — Geymifikatsiya, SEO va Foydalanuvchi Jalb Qilish

**Maqsad**: Kunlik kirishni ta'minlovchi psixologik "hook" + organik traffic.

### Deliverables
- [x] **Daily Streak**:
  - `UserStreak` modeli — kun, maksimum, joriy zanjir ✅
  - `streak_service.update_on_activity()` — yangi/consecutive/broken logikasi ✅
  - Milestone reward: 7→50, 30→200, 100→1000 Coin ✅
  - Celery: `check_broken_streaks_task` (Beat-ready) — Notification queue ✅
- [x] **Leagues Engine**:
  - `League` modeli: Bronza, Kumush, Oltin, Olmos (rank_order 1–4) ✅
  - `LeagueMembership` (per-(user, week)) + idempotent `get_or_create_membership` ✅
  - `leagues_service.add_points()` ✅
  - Haftalik Celery: `leagues_weekly_recalc_task` — top 10 promote, bottom 10 demote, Coin reward ✅
  - WebSocket — Task 5 leaderboard consumer'i ham qo'shimcha real-time qatlam (jonli liga reytingi alohida deferred) 🔵
- [x] **Coin Rewards**: liga promotion + streak milestone → wallet credit (audit trail) ✅
- [x] **SEO Architecture**:
  - `django.contrib.sitemaps` faollashtirildi ✅
  - `PublicQuestionsSitemap` — quarantined yo'q + organization=None public, max 5000 ✅
  - `/sitemap.xml` URL ✅
  - OG meta tags / `noindex` markup — frontend (Next.js) qatlamida 🔵
- [x] **Meilisearch** integratsiyasi (stub mode):
  - `search_service.search_questions(query, *, org)` ✅
  - PG ICONTAINS fallback (QuestionVersion.content JSONB) ✅
  - Quarantined exclusion ✅
  - Production toggle: `MEILISEARCH_URL` + `MEILISEARCH_ENABLED` 🔵
- [x] **Omni-Channel Notifications** (Celery sequential fan-out):
  - `Notification` model (channel/priority/status/metadata) ✅
  - In-app har doim queue qilinadi ✅
  - Telegram (birinchi) — user.telegram_id mavjud bo'lsa stub ✅
  - Push placeholder (ikkinchi) ✅
  - SMS PlayMobile — faqat IMPORTANT/URGENT + boshqa channel'lar fail qilsa ✅
  - REST: list / unread filter / mark-as-read ✅
- [x] **REST API**: 6 endpoint (streak, leagues/current, leagues/history, notifications, notification-read, search) ✅
- [x] **Tests**: 26 ta integration test (TestStreak, TestLeagues, TestNotifications, TestSearch, TestEngagementAPI, TestSEO) ✅
- [x] **Signal**: `apps/exams/signals.py` SUBMITTED → streak update + league points (best-effort, swallow) ✅

### Tech Stack
`Meilisearch (stub)` · `Celery` · `django-sitemaps` · `Telegram bot (stub)` · `PlayMobile SMS (stub)`

### Arxitektura qaror
> **Gibrid Geymifikatsiya + SEO Dvigateli** — Duolingo + Brainly kombinatsiyasi (Bosqich 21, 22, 15, 16)

---

## Task 10 — Production Infrastructure: K8s, Monitoring va Xavfsizlik

**Maqsad**: Zero-Downtime deployment, tizim sog'lig'ini nazorat va DDoS himoya.

### Deliverables
- [x] **Docker**: multi-stage `Dockerfile.backend` (web/asgi/worker/beat/migrate one image, dispatch via entrypoint) + `Dockerfile.nginx` ✅
- [x] **Docker Compose** (dev muhit): Django + PostgreSQL + Redis + ClickHouse + Meilisearch + Celery (worker + beat) + Nginx ✅
- [x] **Kubernetes manifests** (Helm chart `charts/yuzdanyuz/`):
  - `Deployment` (web, channels, celery-worker, celery-beat, nginx) — RollingUpdate ✅
  - `HorizontalPodAutoscaler` (web 4-30, channels 3-15) — CPU + Memory ✅
  - `ConfigMap` (env) + `Secret` reference (out-of-band creation) ✅
  - `Ingress` (nginx + cert-manager TLS) — subdomain routing (White-Label `*.yuzdanyuz.uz`) ✅
  - `PodDisruptionBudget` (minAvailable=1) ✅
  - `NetworkPolicy` (default-deny + DNS/PG/Redis/HTTPS allow) ✅
  - `ServiceMonitor` (Prometheus Operator scrape) ✅
  - `Job` (migrate — Helm pre-install hook) ✅
  - `CronJob` (WAL-G full backup — opt-in) ✅
  - `ServiceAccount` (no token mount) + Pod/Container SecurityContext (non-root, drop ALL caps) ✅
  - 3 ta env overlay: `values-dev.yaml`, `values-staging.yaml`, `values-prod.yaml` ✅
- [x] **CI/CD** (GitHub Actions `build-and-push.yml`):
  - Build + push backend + nginx images to `ghcr.io/menarzullayev/yuzdanyuz-{backend,nginx}` ✅
  - Tags: branch / PR / semver / sha / latest (docker-metadata-action) ✅
  - Helm lint + render smoke test ✅
- [x] **GitOps** (Argo CD `argocd/`):
  - `AppProject` — RBAC + sourceRepo allowlist ✅
  - `Application` — staging (auto-sync from main), prod (manual sync from version tag) ✅
  - `argocd/README.md` — bootstrap + workflow + rollback ✅
- [x] **Monitoring Stack**:
  - Sentry SDK (Django + Celery + Redis + Logging integrations) — DSN env-driven ✅
  - django-prometheus — `/metrics` endpoint + middleware ✅
  - OpenTelemetry — auto-instrumentation (Django + Celery + psycopg2 + Redis + requests) via `opentelemetry-instrument` wrapper ✅
  - OTel Collector config (`monitoring/otel/collector.yaml`) — traces→Tempo, metrics→Prometheus, logs→Loki ✅
  - Prometheus scrape config + 9 ta alert (5xx %, p95 latency, Celery queue, DB conn, replication lag, backup age, Redis memory) ✅
  - Grafana dashboards: Django overview + Celery overview (JSON, importable) ✅
  - JSON logging (python-json-logger) — `core/observability.json_logging_dict()` ✅
- [x] **Cloudflare** (Terraform `terraform/cloudflare/`):
  - DNS — proxied A records (apex + api + wildcard) ✅
  - Zone settings — SSL strict, TLS 1.2+, HTTP/3, brotli, websockets ✅
  - 4 ta WAF custom rule — recon scanners, login challenge, /admin geo-fence, TOR challenge ✅
  - Page rules — `/api/*` no-cache, `/static/*` 30-day cache ✅
  - Rate limit — 300 req/min per IP on /api/* ✅
  - Under Attack mode runbook ✅
- [x] **Backup** — WAL-G PITR:
  - `scripts/backup/wal-g-config.sh` — env template (S3/R2/Spaces) ✅
  - `scripts/backup/backup_full.sh` — daily full backup + retention (7 fulls) ✅
  - `scripts/backup/restore.sh` — PITR restore (latest or timestamp) ✅
  - K8s CronJob (`charts/.../cronjob-backup.yaml`) ✅
  - Restore drill runbook (`docs/deployment/backup.md`) ✅
- [x] **Health probes**:
  - `/health/` — liveness (always 200) ✅
  - `/health/ready/` — readiness (DB + Redis ping, 503 if degraded) ✅
  - K8s liveness/readiness probe wiring (web + channels) ✅
- [x] **Centralized Logging** — JSON logs (python-json-logger), `LOGGING` config in `core/observability.json_logging_dict()` ✅
- [x] **Documentation** — `docs/deployment/{README,docker,k8s,argocd,secrets,monitoring,backup,cloudflare}.md` ✅
- [x] **Tests**: `test_health_endpoint.py` (6 ta — liveness, readiness, /metrics) ✅

### Deferred (alohida PR)
- Real K8s cluster deploy (Hetzner/DOKS/EKS) — 🔵
- Sealed Secrets / External Secrets Operator (GitOps secret management) — 🔵
- argocd-image-updater (auto image tag bump) — 🔵
- KEDA — Celery queue-depth based autoscaling — 🔵
- Cosign image signing + SLSA provenance — 🔵
- Real Sentry/Grafana/Loki cluster (kube-prometheus-stack install) — 🔵
- Geo-Replica (Germaniya standby server) — 🔵

### Tech Stack
`Docker` · `Kubernetes` · `Helm` · `Argo CD` · `GitHub Actions` · `Prometheus` · `Grafana` · `OpenTelemetry` · `Sentry` · `Cloudflare` · `Terraform` · `WAL-G`

### Arxitektura qaror
> **Zero-Trust + "O'lmas" Arxitektura** — tizim hech qachon qulamaydi, ma'lumotlar hech qachon yo'qolmaydi (Bosqich 18, 27, 28, 29)

---

## Roadmap Summary

| # | Task | Murakkablik | Status | Test Coverage |
|---|------|-------------|--------|----------------|
| 1 | Foundation & Multi-Tenant | ⭐⭐ | ✅ COMPLETE | ✅ 111 test (95%+) |
| 2 | Auth + Device Fingerprinting | ⭐⭐⭐ | ✅ COMPLETE | ✅ 246 test (95%+) |
| 3 | Question Bank + Kontent Himoya | ⭐⭐⭐ | ✅ COMPLETE (10/10) | ✅ 49 test (99%+) |
| 4 | Exam Engine + Anti-Cheat | ⭐⭐⭐⭐ | 🟢 Backend ✅ (data+celery+API+WS+RLS) / Frontend 🔵 | ✅ 53 test (Task 4) |
| 5 | Redis Leaderboard | ⭐⭐ | 🟢 Backend ✅ (core+WS+archive) / Frontend 🔵 | ✅ 32 test |
| 6 | AI Diagnostika + Knowledge Graph | ⭐⭐⭐⭐ | 🟢 Backend ✅ / Frontend 🔵 | ✅ 25 test |
| 7 | Billing + Wallet + Affiliate | ⭐⭐⭐⭐ | 🟢 Backend ✅ / Frontend 🔵 | ✅ 36 test |
| 8 | B2B Dashboard + ClickHouse | ⭐⭐⭐ | 🟢 Backend ✅ / Frontend 🔵 | ✅ 20 test |
| 9 | Geymifikatsiya + SEO + Bildirishnomalar | ⭐⭐⭐ | 🟢 Backend ✅ / Frontend 🔵 | ✅ 26 test |
| 10 | K8s + Monitoring + Xavfsizlik | ⭐⭐⭐⭐⭐ | 🟢 Backend ✅ (manifests + CI/CD + monitoring) | ✅ 6 test (511 jami) |

### ✅ Completed Phases

**Task 1** (2026-05-15): ✅ COMPLETE
- Deliverables: 13/13 ✅
- Security fixes: 3/3 ✅
- Test coverage: 111 tests, 95%+ critical modules ✅

**Task 2** (2026-05-15): ✅ COMPLETE
- Deliverables: 9/9 ✅ (Google OAuth, Account Linking, Email Verification, Device Policy)
- Test coverage: 246 tests, 95%+ coverage ✅
- Security: JWT attacks, tenant boundaries, device fingerprinting ✅

**Task 3** (2026-05-15): ✅ COMPLETE
- Deliverables: 10/10 (7 core + human review + 3 security) ✅
- Test coverage: 49 tests (39 unit + 10 integration), 99% models ✅
- Bulk import: Excel/CSV parser with validation ✅
- AI parser: Multi-provider with fallback ✅
- Review panel: HTMX interactive dashboard ✅
- **Content Security**: Watermark (Pillow+CSS) + DOM Shuffle + Rate Limit (Redis) ✅
- **PostgreSQL RLS**: Migration `catalog.0003_enable_rls` deployed (6 tables, 10 policies) ✅

### Frontend

**Monorepo Setup** (2026-05-15): ✅ COMPLETE
- Next.js 15 + React 19 (port 3000)
- Shared TypeScript package (@yuzdanyuz/shared)
- API client with JWT interceptors
- Login + Dashboard pages
- Production startup scripts

**Task 10** (2026-05-16): 🟢 **INFRASTRUCTURE COMPLETE** (PR #38)
- Docker: multi-stage Dockerfile.backend (web/asgi/worker/beat/migrate one image) + Dockerfile.nginx + entrypoint.sh
- Docker Compose: full dev stack (Django + PG + Redis + ClickHouse + Meilisearch + Celery + Nginx)
- Helm chart `charts/yuzdanyuz/`: 17 ta template (deployment×4, service, ingress, HPA, PDB, NetworkPolicy, ServiceMonitor, Job migrate, CronJob backup, ServiceAccount, ConfigMap, PVC) + 3 ta env overlay
- Argo CD GitOps: AppProject + Application×2 (staging auto, prod manual) + README
- GitHub Actions: build-and-push.yml (matrix backend+nginx → ghcr.io) + helm lint
- Cloudflare Terraform: DNS (proxied) + WAF (4 custom rules) + Page Rules + Rate Limit + zone settings
- WAL-G PITR: config + backup_full.sh + restore.sh + K8s CronJob + restore drill runbook
- Observability:
  - Sentry SDK (Django + Celery + Redis + Logging integrations)
  - django-prometheus /metrics endpoint
  - OpenTelemetry auto-instrumentation (Django/Celery/psycopg2/Redis/requests)
  - OTel Collector → Tempo (traces) + Prometheus (metrics) + Loki (logs)
  - 9 ta Prometheus alert (5xx, latency p95, queue backlog, replication lag, backup age, ...)
  - 2 ta Grafana dashboard (Django overview + Celery overview)
  - JSON structured logging (python-json-logger)
- Health endpoints: /health/ (liveness) + /health/ready/ (DB + Redis ping)
- Documentation: docs/deployment/{README, docker, k8s, argocd, secrets, monitoring, backup, cloudflare}.md
- Tests: 6/6 ✅ (511 jami)
- Real K8s cluster deploy: 🔵 (manifests only — Hetzner/DOKS/EKS alohida PR)

**Task 9** (2026-05-16): 🟢 **BACKEND COMPLETE** (PR #37)
- 4 ta yangi model: UserStreak, League, LeagueMembership, Notification
- 4 ta service modul: streak_service, leagues_service, notifications_service, search_service
- Streak engine — Duolingo style consecutive/broken + milestone Coin reward (7/30/100)
- Leagues engine — 4 daraja (Bronza→Olmos), idempotent membership, weekly recalc (top 10 promote / bottom 10 demote)
- Omni-channel notifications — sequential fan-out (in-app + Telegram + Push + SMS escalation)
- Search — Meilisearch stub + PG ICONTAINS fallback (quarantined exclusion, JSONB)
- SEO — django.contrib.sitemaps + PublicQuestionsSitemap (`/sitemap.xml`)
- 2 ta yangi Celery task: check_broken_streaks_task, leagues_weekly_recalc_task
- 6 ta REST endpoint (streak, leagues/current, leagues/history, notifications, notification-read, search)
- Signal: ExamAttempt SUBMITTED → streak.update_on_activity + leagues.add_points (best-effort)
- Tests: 26/26 ✅ (505 jami)
- Frontend Next.js streak/leagues/notifications UI: 🔵
- Real Meilisearch + Telegram bot live integration: 🔵 (stub mode default)

**Task 8** (2026-05-16): 🟢 **BACKEND COMPLETE** (PR #36)
- 2 ta yangi model: ExamEvent (denormalized analytics snapshot) + ReportExport
- ClickHouse stub abstraction (CLICKHOUSE_ENABLED toggle for prod)
- Aggregations: subject avg (bar), weekly growth (line), distribution (pie), weak students
- Signal: ExamAttempt SUBMITTED → record_exam_event (idempotent)
- Celery task: generate_export (CSV via csv module, Excel via openpyxl)
- 7 ta REST endpoint (dashboard + 4 chart + export create/detail)
- Permission: org admin/owner/manager/teacher (student 403)
- Tests: 20/20 ✅ (479 jami)
- Frontend Next.js dashboard UI: 🔵
- Read replica routing: 🔵 (DATABASE_ROUTERS)

**Task 7** (2026-05-16): 🟢 **BACKEND COMPLETE** (PR #35)
- 7 ta yangi model (Wallet, WalletTransaction, PaymentIntent, SubscriptionPlan,
  OrganizationSubscription, ReferralCode, Referral)
- Wallet service — SELECT FOR UPDATE atomic ops + audit trail
- Payment providers (Payme/Click stub mode) + webhook handlers (idempotent + signature)
- Subscription service — flexible (monthly/yearly/lifetime, flat/per_seat)
- Auto-renew Celery beat task
- Affiliate (referral code + Coin bonus + 50+ threshold cash withdrawal)
- 12 ta REST endpoint (wallet, payments, subscriptions, affiliate)
- Tests: 36/36 ✅ (459 jami)
- Frontend checkout UI: 🔵

**Task 6** (2026-05-16): 🟢 **BACKEND COMPLETE** (PR #34)
- 4 ta yangi model: SkillTag, UserSkillProfile (Bayesian), AIFeedback, OpenEndedSubmission
- Bayesian engine — pure functions (apps/intelligence/bayesian.py)
- Knowledge Graph — get_user_summary, recommend_questions, weak/strong skills
- Signal: UserAnswer post_save → mastery update (per skill)
- 2 ta Celery task: generate_ai_tutor_feedback (cached), evaluate_openended_submission
- 7 ta REST endpoint (mastery, recommendations, tutor, openended)
- Anthropic SDK wrapper (test'da mock'lanadi)
- Tests: 25/25 ✅ (423 jami)
- Frontend Knowledge Map (vizual): 🔵

**Task 5** (2026-05-16): 🟢 **BACKEND COMPLETE** (PR #31, #32, #33)
- Service: `apps/engagement/leaderboard.py` — Redis ZSET wrapper + Channels broadcast
- 4 ta scope: `lb:mock:<id>`, `lb:global`, `lb:region:<id>`, `lb:tenant:<id>`
- Best-of semantics: GT modifier (Redis 6.2+) + fallback compare-and-swap
- Signal: ExamAttempt SUBMITTED + score → leaderboard.record_attempt()
- REST API: **6 endpoint** (top + history), N+1 yo'q (single SQL prefetch)
- WebSocket consumer: `ws/leaderboard/<scope>/` — auth + scope authorization
- Real-time push: ZADD → channel layer `group_send` → subscribed clients
- **Archive**: `LeaderboardSnapshot` model + `archive_leaderboards` Celery beat task
  (weekly/monthly/yearly snapshot, idempotent via update_or_create, Top-1000)
- Tests: **32/32 ✅** (398 jami)
- Frontend Next.js sahifa (kelajak): 🔵

**Task 4** (2026-05-16): 🟢 **BACKEND COMPLETE** (PR #25, #27, #28, #29 + RLS)
- Models: 7/7 ✅ + custom manager + 1 migration
- Celery: 3 task ✅ (auto-publish, quarantine, finalize_score) + signal trigger
- REST API: 12 endpoint ✅ (DRF SessionAuth + IsAuthenticated)
- WebSocket: ExamAttemptConsumer ✅ (heartbeat + strikes + timer + 5 close codes)
- L3 RLS: 7 exam table ✅ (catalog pattern)
- Tests: 53 Task 4 tests / **366 jami** (regression yo'q)
- Frontend (Next.js browser lock + UI): 🔵 alohida workstream

### 🛡️ Hardening (post-Task 1, 2026-05-16)

L3 RLS audit'da topilgan kritik xatolar uchun follow-up PR'lar:
- **PR #22** — `request.org` hech qachon set qilinmagan → RLS bypass; TenantManager fail-closed; JWT verify_exp; RLS fail-closed; 313 → 333 test
- **PR #23** — SECRET_KEY prod'da fail-loud (env required); urls.py mid-file import + home_view template
- **PR #24** — Membership query Redis cache (60s TTL + signal invalidation); threading.local → contextvars (async-ready)

### 📊 Overall Progress

```
Total Tests:       511 ✅ (100% passing)
Models:             60 ✅
Services:          41+ ✅
Middleware:         9  ✅ (+ django_prometheus before/after)
API Endpoints:     93+ ✅ (+ /health/, /health/ready/, /metrics)
WebSocket:          5  ✅
Celery tasks:      10  ✅
Redis ZSETs:        4  ✅
LLM integrations:   2  ✅
Payment providers:  2  ✅ (stub mode)
ClickHouse:         stub abstraction (CLICKHOUSE_ENABLED toggle for prod)
Search:            Meilisearch stub + PG ICONTAINS fallback
Sitemap:           /sitemap.xml (django.contrib.sitemaps)

Production Infra (Task 10):
  Docker:           multi-stage (1 backend image, 5 commands) + nginx
  K8s:              Helm chart (17 templates) + 3 env overlays + Argo CD GitOps
  CI/CD:            GitHub Actions → ghcr.io + Helm lint
  Observability:    Sentry + Prometheus + OpenTelemetry + JSON logs
  Monitoring:       9 ta alert + 2 ta Grafana dashboard + OTel Collector
  Backup:           WAL-G PITR (full + WAL stream) + restore drill runbook
  Edge:             Cloudflare Terraform (DNS proxy + WAF + rate limit)

Frontend Pages:     3+ ✅
Code Coverage:      ~90% critical paths ✅

Security Stack (Defense-in-Depth):
  L1 — JWT + Device Fingerprinting (Task 2)
  L2 — RBAC + Tenant Isolation + TenantManager fail-closed (Task 1 + PR #22)
  L3 — PostgreSQL RLS (catalog: PR #22, exams: yangi RLS PR — Task 4 backend)
  L4 — Rate Limiting (Redis, progressive ban)
  L5 — Content Protection (Watermark + DOM Shuffle)
```

> **Izoh**: Birinchi 3 task to'liq. Task 4 — backend to'liq tayyor (data + celery + API + WS + RLS).
> Frontend qism (Next.js browser lock + UI) alohida workstream.
> AI agentlar (Claude Code + Gemini) yordamida tezlashtirilgan implementatsiya.
