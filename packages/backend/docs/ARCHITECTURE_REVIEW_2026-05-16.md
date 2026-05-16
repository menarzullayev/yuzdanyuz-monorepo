# YuzDanYuz Backend — Senior Architecture Review

> **Audit sanasi**: 2026-05-16
> **Hajm**: 8 app, 46 model, 17 servis, 10 Celery task, 6 signal, 2 WebSocket consumer, 19 middleware, 512 test, ~440 satr settings, Task 1–10 to'liq
> **Maqsadli ko'lam**: 1M+ foydalanuvchi, multi-region, multi-team, enterprise B2B
> **Metodologiya**: 4 ta parallel Explore agent → 8000+ satr xom topilmalar → CTO/Staff Architect darajasidagi sintez

---

## TL;DR — Eng katta 5 ta risk

| # | Risk | Severity | Production'da ta'siri |
|---|---|---|---|
| 1 | **`CustomUser` o'chirilganda CASCADE moliyaviy + audit ma'lumotlarni yo'q qiladi** (Wallet, Payment, UserAnswer, ExamEvent) | 🔴 P0 | GDPR buzilishi, jarima ($20M+), qaytarib bo'lmaydigan ma'lumot yo'qotish |
| 2 | **N+1 har bir list/detail view'da** (apps/*/views.py ichida 0 ta `select_related`/`prefetch_related`) | 🔴 P0 | 100K DAU'da: PG connection pool tugashi, p95 > 5s, xarajat portlashi |
| 3 | **Single-replica `celery-beat` + distributed lock yo'q** (broken streak notifications, league recalc) | 🔴 P0 | Failover → notification duplicate, SMS bill spike, foydalanuvchi ishonchini yo'qotish |
| 4 | **HTMX + REST aralash bir view fayllarda, API versioning yo'q, OpenAPI yo'q** | 🟠 P1 | Frontend jamoa bloklangan (contract yo'q), mobile app har release'da qayta yoziladi |
| 5 | **Implicit state machine'lar (7 ta `status` enum model, FSM library yo'q, tranzitsiyalar tarqoq)** | 🟠 P1 | Race-condition bug'lar ko'payadi, biznes mantig'i auditga yaroqsiz |

---

## 1. 🔴 Arxitektura zaifliklari

### 1.1 Domain-Driven layout, lekin Django signal'lar orqali apps aro coupling — yashirin dependency to'ri

**Fayllar**: `apps/exams/signals.py:33-86` — bitta `update_leaderboard_on_submit` handler 5 ta cross-app servisni sinxron chaqiradi (engagement, analytics, intelligence, commerce indirect orqali streak). `try/except` bilan o'ralgan, lekin xato ko'rinmaydigan bo'lib qoladi.

**Ildiz sabab**: Django signal'lari kambag'al-odam event bus sifatida ishlatilgan. Signal'lar sinxron, typed emas, ordering arbitrary, izolyatsiyada test qilish imkoni yo'q.

**Uzoq muddatli oqibat**:
- 6-chi consumer qo'shish (masalan "o'qituvchiga Slack xabari") `exams/signals.py`'ni tahrirlashni talab qiladi — `engagement`'ga tegishli coupling `exams`'ga sizib chiqadi
- Jamoa 2+ pod'gacha kengaysa (exams team vs engagement team), shu fayldagi merge conflict'lar avj oladi
- Multi-region: signal handler faqat yozuvchi region'da ishlaydi → cross-region eventual consistency Kafka/SNS bridge talab qiladi

**Refactor strategiyasi** (3 bosqich):
1. **Hozir** (~1 kun): Signal'dagi to'g'ridan-to'g'ri service call'larni bitta `events.publish("exam.submitted", attempt)` chaqiruvi bilan almashtirish
2. **Q2** (~1 sprint): `apps/events/` modulini joriy etish — typed event class'lar (Pydantic), `blinker` bilan in-process pub/sub + Celery dispatcher
3. **Q4** (~2 sprint): In-process bus'ni Redis Streams yoki AWS EventBridge bilan almashtirish — typed contract saqlanadi, multi-region fanout imkoniyati ochiladi

**Production reference**: Stripe ichki event taksonomiyasi (typed event nomlari, immutable payload, har event'da idempotency_key). Shopify ActiveSupport::Notifications'ni typed in-process bus sifatida async dispatch'dan oldin ishlatadi.

### 1.2 HTMX + REST aralash bir view fayllarda — arxitektura chegarasi yo'q

**Fayllar**: `apps/exams/views.py`'da 12 ta `View` (HTMX) + 12 ta `APIView` (REST) aralash. `apps/accounts/views/login_views.py:137-186` `request.POST.get('login')` ishlatadi (HTMX form-encoded) — aynan o'sha domen mobile app uchun JSON-based bo'lishi kerak.

**Ildiz sabab** (Lesson 12 buni tutdi): Backend HTMX-first qurilgan; REST endpoint'lar shu view modullariga retrofit qilingan. Web SSR view va JSON API o'rtasida aniq contract yo'q.

**Production'da oqibat**: Hozir mobile RN app qayta yozayotgan frontend jamoa IKKI ta request pattern (HTMX form-encoded + JSON), har ikkalasi uchun CSRF token tantanasi va field nomlarini topish uchun view source kodini o'qishni o'rganishi kerak (OpenAPI yo'q).

**Refactor**:
```
apps/exams/
  views/
    htmx.py        # @ensure_csrf_cookie, request.POST, returns partials
    api.py         # APIView, request.data, returns serialized JSON
  urls/
    htmx.py        # /exams/...
    api.py         # /api/v1/exams/...
```
`drf-spectacular` faqat `api.py`'ga qo'llaniladi. HTMX view'lar public API contract'idan tushadi.

**Production reference**: GitHub 2008-yilda `https://github.com/...` HTML va `https://api.github.com/...` JSON'ni alohida routing tree'larga ajratgan; hech qachon afsuslanmagan.

### 1.3 Aniq FSM yo'q — 7 ta `status` enum modeli, tranzitsiyalar tarqoq

**Fayllar**: `MockExam`, `ExamAttempt`, `PracticeSession`, `QuestionDispute`, `OrganizationSubscription`, `PaymentIntent`, `Notification` — har bir state transition `if status == X: status = Y` ko'rinishida view/service/task'lar bo'ylab tarqoq.

**Production'da risk**: Allaqachon kuzatilgan:
- `ExamAttempt.cancelled` AntiCheat tomonidan 3 strike'da o'rnatiladi, lekin keyingi strike check ad-hoc bloklanadi (Bosqich 6 S10.4 ko'rsatdi 400 "Attempt holati: cancelled" — defensive lekin tasodifiy)
- `PaymentIntent.SUCCEEDED` ikki yo'ldan o'rnatilishi mumkin (webhook + manual admin) — transition log yo'q

**Refactor**: `django-fsm` yoki `django-fsm-2`'ni qabul qilish. Migratsiya mexanik — `@transition` dekoratorlarini qo'shish:
```python
class ExamAttempt(TenantTimestampMixin):
    status = FSMField(default='in_progress', protected=True)

    @transition(field=status, source='in_progress', target='submitted')
    def submit(self):
        self.submitted_at = timezone.now()

    @transition(field=status, source='*', target='cancelled',
                conditions=[lambda self: self.strikes >= 3])
    def cancel_for_cheating(self, reason):
        self.cancel_reason = reason
```
Yon ta'sir: Django admin ruxsat etilgan tranzitsiyalarni tugma sifatida ko'rsatadi.

**Production reference**: Shopify Orders state machine (12 state, 23 transition) FSM sifatida kodlangan va har transition `OrderStateLog` bilan audit qilinadi.

---

## 2. 🔴 Scalability bottleneck'lari

### 2.1 N+1 HAR BIR view'da — apps/*/views.py'da 0 ta `select_related`/`prefetch_related`

**Severity**: 🔴 P0 — scaling uchun bloklovchi omil

**Fayllar**: Agent buni sistemali topdi:
- `apps/commerce/views.py:67` — `wallet.transactions.all()[:100]` payment_intent/user'ga select_related yo'q
- `apps/commerce/views.py:357` — `Referral.objects.filter(inviter=user)` ketma-ket 2 marta chaqirilgan (n^2)
- `apps/intelligence/views.py:73` — bir request'da `AIFeedback.filter` bir necha marta bajarilishi mumkin
- Barcha exam attempt view'lari attempt → exam → questions[] olib keladi prefetch'siz

**1M foydalanuvchi / 100K DAU'da**:
- O'rtacha wallet history request: 1 (wallet) + 100 (transactions) + 100 (payment_intent FK) = **201 query**
- PostgreSQL connection pool (`pgbouncer` odatda 100 ulanish) ~5-10 RPS'da tugaydi
- p95 latency: prefetch bilan 50ms o'rniga 3-8 soniya
- Database CPU peg → vertical scaling xarajati (masalan db.r6g.4xlarge → 8xlarge = +$1500/oy)

**Refactor** (1 sprint):
1. Dev'da `django-perf-rec` + `nplusone` o'rnatish — N+1'da test fail bo'ladi
2. Kritik path'larda `nplusone` ishga tushiradigan pre-commit hook qo'shish
3. views.py fayllarini tozalash — serializer'da FK'ga tegadigan har bir queryset `.select_related()` yoki `.prefetch_related()` oladi
4. CI: integration test sozlamalarida `nplusone` strict mode

**Production reference**: Strava'ning blogida `select_related`'ni hamma joyga qo'shish p99'ni 60% kamaytirgani va DB upgrade'ni 18 oyga kechiktirgani aytilgan. Sentry'ning `n+1 query` detektori endi buni avtomat tutadi.

### 2.2 Bitta Celery queue barcha task turlari uchun

**Fayllar**: `core/settings/base.py:84-96` — `CELERY_TASK_TIME_LIMIT=300` global, queue routing yo'q. AI tutor (Anthropic'ga 10s chaqiruv) `finalize_attempt_score` (50ms DB yozish) va `archive_leaderboards` (30s skan) bilan bir queue'ni baham ko'radi.

**Scale'da**: 100 foydalanuvchi bir vaqtda exam topshirsa va 5 tasi AI tutor'dan foydalansa:
- 5 ta AI task 5 ta worker slot'ni 10s blokirlaydi
- 95 ta attempt finalize task navbatda turadi
- Leaderboard yangilanishi 30s+ kechikadi → foydalanuvchi eskirgan rank ko'radi
- Frontend "submit → leaderboard darhol yangilanadi" deb taxmin qiladi

**Refactor**:
```python
# core/celery.py
app.conf.task_routes = {
    'exams.finalize_attempt_score': {'queue': 'critical'},     # latency-sensitive
    'engagement.fan_out_notification': {'queue': 'realtime'},  # user-facing
    'intelligence.*': {'queue': 'ai'},                         # slow + costly
    'engagement.archive_leaderboards': {'queue': 'batch'},     # background
    'commerce.auto_renew_subscriptions': {'queue': 'batch'},
}
```
+ Helm: alohida `celery-worker-critical` (5 replica, concurrency=10), `celery-worker-ai` (2 replica, concurrency=4, uzoqroq timeout), `celery-worker-batch` (1 replica, concurrency=2).

**Production reference**: Asana 14 ta Celery queue'ni har queue uchun aniq SLO bilan ishlatadi ("critical" p95 < 1s, "batch" p95 < 5min). Sentry'da 60+ queue.

### 2.3 Single-replica `celery-beat`, distributed lock yo'q — failover BARCHA jadval'larni qayta ishga tushiradi

**Fayllar**: `charts/yuzdanyuz/templates/deployment-celery-beat.yaml` — `replicas: 1`, `strategy: Recreate`. `engagement.check_broken_streaks` (Lesson 13/Risk audit) **aniq belgilangan**: "no state; resends notification on re-run".

**Scale'da**:
- Beat pod evicted (node drain, OOM, rolling restart)
- Yangi beat pod start qiladi → DB'dan `PeriodicTask.last_run_at` o'qiydi
- Agar last_run_at eviction'dan 60s oldin bo'lsa va beat schedule `*/5 min` bo'lsa — darhol ishga tushadi
- `check_broken_streaks` 5min ichida 2 marta ishlaydi → har "broken streak" foydalanuvchi 2 ta SMS notification oladi
- 100K foydalanuvchi × 5% broken = 5000 × 2 = 10K SMS = $500 isrof + foydalanuvchi shikoyatlari

**Refactor** — bittasini tanlang:
1. **Arzon fix** (1 kun): Task boshida Redis lock qo'shish:
   ```python
   from redis_lock import Lock
   def check_broken_streaks_task():
       with Lock(redis_client, "lock:streak_check", expire=600, auto_renewal=False):
           ...
   ```
2. **To'g'ri fix** (1 sprint): Scheduled task'lar uchun **KEDA + ScaledJob**'ga migratsiya. Har cron entry K8s `CronJob` bo'ladi `concurrencyPolicy: Forbid` bilan. Beat keraksiz bo'lib qoladi.

**Production reference**: Airbnb scheduled job'lar uchun Apache Airflow ishlatadi (built-in execution_date deduplication). Ko'p SaaS kompaniyalari `celery-beat`'ni `temporal.io` bilan almashtirgan — guaranteed-once execution uchun.

### 2.4 PostgreSQL RLS qoplami: 46 ta jadvaldan 13 tasi (28%)

**Fayllar**: `apps/catalog/migrations/0003_enable_rls.py` (6 jadval), `apps/exams/migrations/0002_enable_rls.py` (7 jadval). Yetishmaydi: BARCHA `engagement.*`, `commerce.*`, `intelligence.*`, `analytics.*`.

**Risk**: L3 RLS — application bug'lariga qarshi oxirgi mudofaa chizig'i. `WalletTransaction`'da bo'lmasa:
- `wallet_service.spend`'dagi bug boshqa org'ning tranzaksiyalarini ochib qo'yishi mumkin
- CLAUDE.md'da va'da qilingan 100% PG-darajadagi kafolat 28% bajarilgan

**Refactor**: Har app uchun 1 migration — to'g'ri, mavjud shablonga amal qiladi.

---

## 3. 🟠 Kelajakdagi kengayish cheklovlari

### 3.1 Tenant-overridable config — va'da qilingan, amalga oshirilmagan

**Fayllar**: `apps/organizations/models.py` — `Organization.settings = JSONField(default=dict)`. `org.settings.get(...)` orqali **0 ta foydalanish** topildi.

**Kodda yashiringan magic constant'lar** (per-tenant bo'lishi kerak):
- `QUARANTINE_MIN_DISPUTES = 5` (exams/tasks.py:25)
- `MAX_STRIKES = 3` (exams/consumers.py:35)
- `HEARTBEAT_TIMEOUT_SECONDS = 60` (exams/consumers.py:36)
- `MILESTONE_REWARDS = {7: 50, 30: 200, 100: 1000}` (engagement/streak_service.py)
- `PROMOTE_TOP=10`, `DEMOTE_BOTTOM=10` (engagement/leagues_service.py)

**B2B sales haqiqati**: Enterprise mijoz aytadi "biz uchun 1-strike-out bo'lsin (high-stakes sertifikat imtihoni uchun)". Siz yoki:
- "v2'ni kuting" deb javob berasiz
- Maxsus holat sifatida hardcode qilasiz (technical debt)

**Refactor**:
```python
# core/config.py
def get_org_setting(org, key, default):
    if org and key in org.settings:
        return org.settings[key]
    return getattr(settings, f'DEFAULT_{key}', default)

# Foydalanish
MAX_STRIKES = get_org_setting(self.attempt.organization, 'exam.max_strikes', 3)
```
+ `org.settings` JSON'ni tahrirlash uchun Admin UI (yoki schema-typed django-jsonform orqali).

**Production reference**: Salesforce'da 1000+ org-darajadagi sozlama (har biri default + override + audit log bilan). Auth0 ularni "tenant settings" deb ataydi va enterprise mijozlarga API orqali override qilishga ruxsat beradi.

### 3.2 Plugin/modular arxitektura yo'q — har yangi feature kod commit'i

**Pattern**: AIProviderConfig (catalog/models.py:215) plugin-like config'ga ishora qiladi, lekin tizimning qolgan qismi hardcoded. Yangi payment provider qo'shish = `commerce/payment_providers.py`'da yangi fayl.

**Kelajakdagi cheklov**:
- Enterprise hamkorlarga "kengaytmalar" ochish mumkin emas (masalan custom anti-cheat moduli)
- Kod deploy qilmasdan org'lar to'plamiga feature flag yuborish mumkin emas

**Refactor**: **stevedore**'ni (OpenStack plugin pattern) qabul qilish: payment providers, SMS backend'lar, AI provider'lar, anti-cheat strategiyalari uchun. Har plugin Python entrypoint.

**Production reference**: Sentry data scrubber'lari, Airflow operator'lari, Django REST framework auth class'lari — barchasi entrypoint-based.

### 3.3 API versioning yo'q

**Fayllar**: `core/urls.py:35-38` — `/api/exams/`, `/api/leaderboard/`, etc. `/v1/` prefix yo'q. RateLimit middleware'da `/api/v1/auth/login`'ga havola qiluvchi o'lik kod bor.

**Kelajakdagi cheklov**: Mobile app v1.5 production'da `selected: int` bilan; siz multi-choice uchun `selected: list[int]` xohlaysiz. **Yetkazib berilgan mijozlar uchun breaking change = customer support to'lqini.**

**Refactor**:
```python
# core/urls.py
path('api/v1/exams/', include('apps.exams.urls')),
path('api/v2/exams/', include('apps.exams.urls_v2')),  # kelajak
```
Versioning header'lar HAM qo'llab-quvvatlanadi: `Accept: application/vnd.yuzdanyuz.v1+json`.

**Production reference**: Stripe har API chaqiruvini sana bilan versiyalashtiradi (`Stripe-Version: 2024-11-20`). Twilio URL versioning'ni ishlatadi `/2010-04-01/...`.

---

## 4. 🟡 Maintainability muammolari

### 4.1 Service test qoplami: 13 ta servisdan 4 tasida direct unit test bor

**Fayllar**: `tests/unit/test_wallet_service.py`, `test_subscription_service.py`, `test_affiliate_service.py`, `test_streak_service.py`, `test_leagues_service.py`, `test_leaderboard.py`, `test_notifications_service.py` — yo'q. Faqat API integration orqali test qilinadi.

**Maintenance og'rig'i**: `wallet_service.spend()`'ni refactor qilish uni ishlatadigan BARCHA `commerce/views.py` flow'larini tushunishni talab qiladi. Service contract unit test bilan qotirilmagan.

**Fix**: Service layer uchun unit test qo'shish. Maqsad: ≥80% service qoplami mocked DB bilan (`pytest-django` `@pytest.mark.django_db(transaction=True)` yetarli — wallet ops `select_for_update` ishlatadi).

### 4.2 Bo'sh `services.py` placeholder'lar refactoring'ni yo'ldan ozdiradi

**Fayllar**: `apps/exams/services.py`, `apps/catalog/services.py`, `apps/engagement/services.py`, `apps/organizations/services.py` — bo'sh fayllar. Mantiq esa `tasks.py`, `signals.py`, `views.py`'da yoki aloqasi yo'q `*_service.py` fayllarida.

**Fix**: Yoki bo'sh fayllarni o'chiring, YOKI loyiha qoidasi o'rnatish "services.py majburiy va biznes mantig'i faqat shu yerda yashaydi". View'larda biznes mantig'i bo'lsa fail qiladigan `ruff` qoidasi qo'shish.

### 4.3 22 ta JSONField — 4 tasi (QuestionVersion.content/options/explanation/adaptive_feedback) butun savol data tuzilishini saqlaydi

**Fayllar**: `apps/catalog/models.py:QuestionVersion` — bitta qator 1MB+ bo'lishi mumkin (media + tushuntirish + per-choice feedback bilan essay).

**Uzoq muddatda**:
- "X tasvir URL'siga ega barcha savollarni top" so'rovini bajarib bo'lmaydi
- Schema'ni validate qilib bo'lmaydi (`is_correct` flag'idagi xato yashirin ishlaydi)
- Backup hajmi shishadi — 100M savol × 100KB = 10TB faqat shu jadval uchun
- Normalizatsiyasiz ClickHouse migratsiyasi imkonsiz

**Refactor** (Q3): `QuestionContent`, `QuestionOption`, `QuestionMedia`, `QuestionExplanation` jadvallariga normalizatsiya. JSON snapshot'ni `QuestionVersion`'da legal/audit uchun saqlash ("publish vaqtidagi snapshot"), lekin uni derived/secondary qilish.

---

## 5. 🔴 Xavfsizlik risklari

### 5.1 `CustomUser`'ga CASCADE delete moliyaviy audit zanjirini yo'q qiladi

**Severity**: 🔴 P0 — tartibga solish + huquqiy

**Fayllar**: `WalletTransaction.wallet → Wallet.user (CASCADE)`, `PaymentIntent.user (CASCADE — Wallet user'i cascade bo'lsa)`, `ExamEvent.user (CASCADE)`.

**Stsenariy**: GDPR Article 17 talabi "ma'lumotimni o'chir". Foydalanuvchi akkauntini o'chiradi → `User.delete()` cascade qiladi:
- Barcha `WalletTransaction` qatorlar YO'Q → moliyaviy reconciliation imkonsiz (UZ buxgalteriya qonuni: 5 yil saqlash)
- Barcha `ExamEvent` qatorlar YO'Q → ICDL/DTM compliance audit imkonsiz
- Barcha `OpenEndedSubmission` qatorlar YO'Q → keyinroq nizo bo'lsa "AI adolatli baholadi" deb isbotlash imkonsiz

**Fix**:
1. **Soft-delete mixin** audit modellariga — `is_deleted: bool`, `deleted_at: datetime`, `pii_redacted: bool`
2. GDPR endpoint: `POST /api/gdpr/erasure` → `User.email/phone/name` anonimlashtiradi → audit ID'larni saqlaydi
3. Audit FK uchun PG migratsiyasi `ON DELETE SET NULL`'ga → transaction qatorini saqlash, faqat user attribution'ni yo'qotish

**Production reference**: Stripe `Charge`'ni hech qachon o'chirmaydi, hatto mijoz akkauntini o'chirsa ham — mijoz nomini `[redacted]` bilan almashtiradi. AWS billing data 7 yil immutable.

### 5.2 OTP rate limit per-phone, lekin global IP-darajadagi cap yo'q

**Fayllar**: `accounts/services/otp_service.py:48-64` — rate key: `otp:send_count:{phone}` (10 daqiqada 3 ta).

**Hujum**: Hujumchi bitta IP'dan 1000 ta telefonni aylantiradi, har biriga 1 OTP yuboradi → sizning hisobingizdan 1000 SMS. Eskiz ~50 UZS/SMS olib keladi = 50K UZS = ~$4 per hujum. Soatlik takrorlasa: $96/kun.

**Fix**: IP-tier limit qo'shish (`otp:send_count:ip:{ip}` — soatiga 20 ta) + 5 IP yuborilgandan keyin CAPTCHA gate.

**Production reference**: Twilio Verify API avtomat "fraud guard" qo'llaydi — shubhali IP pattern'larini rad etadi.

### 5.3 WebSocket auth middleware-set state'ga tayanadi, handshake'da aniq token check yo'q

**Fayllar**: `apps/exams/consumers.py:42-56`, `apps/engagement/consumers.py:46-66` — `scope.get('user')` upstream middleware'ga ishonadi.

**Risk**: Kelajakdagi middleware refactor auth path'ini o'zgartirsa, consumer connect anonymous foydalanuvchilar uchun jim muvaffaqiyat qaytarishi mumkin.

**Fix**: `connect()` ichida aniq token validatsiya:
```python
async def connect(self):
    token = self.scope['cookies'].get('access_token')
    user = await sync_to_async(verify_access_token)(token)
    if not user:
        await self.close(code=4401)
        return
    self.scope['user'] = user  # aniq, middleware'dan ishonilmagan
```

### 5.4 `permission_classes=[AllowAny]` orqali webhook CSRF bypass — signature verification audit yo'q

**Fayllar**: `apps/commerce/views.py:203,211` — `PaymeWebhookView`, `ClickWebhookView`. Stub-mode signature verification har qanday bo'sh emas signature'ni qabul qiladi (`apps/commerce/payment_providers.py:verify_webhook_signature`).

**`ENABLE_REAL_PAYMENTS=true` bo'lganda**: stub validation almashtiriladi — lekin production'da stub-mode bo'lib qolsa fail bo'ladigan `tests/security/test_payment_webhook_signature_required.py` yo'q.

**Fix**: Production'da `_verify_signature` stub-mode bo'lib qolsa fail bo'ladigan security test qo'shish.

---

## 6. 🟠 Performance muammolari

### 6.1 Hech qanday joyda Django cache framework foydalanuvi yo'q

**Topilma**: `grep -rn "from django.core.cache" packages/backend/apps/` → bo'sh. Redis to'g'ridan-to'g'ri session, leaderboard, rate-limit uchun ishlatiladi — odatdagi "qimmat hisob-kitobni cache qilish" holatlari uchun emas.

**Foyda olishi mumkin bo'lgan hot path'lar**:
- `Organization.get_effective_settings()` — har request'da chaqiriladi, cache yo'q
- `OrgRole.permissions` JSON parsing — har permission check'da
- `get_user_summary(user)` (intelligence) — Bayesian aggregation, AI tutor tomonidan har safar chaqiriladi
- `analytics.get_subject_averages(org)` — har dashboard yuklashida DB skan

**Refactor**:
```python
@cached(ttl=300, key_prefix=lambda org: f"org_settings:{org.id}")
def get_effective_settings(self):
    ...
```
`Organization.save()` signal orqali invalidate qilish.

### 6.2 Prometheus'da business metrics yo'q — faqat HTTP/DB default'lar

**Fayllar**: Agent 0 ta `Counter()`/`Gauge()`/`Histogram()` chaqiruvini tasdiqladi. SRE quyidagilarda ko'r holatda:
- Daqiqada topshirilgan imtihonlar
- Provider bo'yicha payment muvaffaqiyat foizi
- Savol turi bo'yicha AI tutor latency
- Leaderboard yangilanish lag

**Fix**: Service layer'da `prometheus_client` metrika chaqiruvlarini qo'shish:
```python
from prometheus_client import Counter, Histogram
EXAMS_SUBMITTED = Counter('yz_exams_submitted_total', 'Submitted exams', ['org_id', 'is_public'])
AI_TUTOR_LATENCY = Histogram('yz_ai_tutor_seconds', 'AI tutor task duration')

# Task ichida
EXAMS_SUBMITTED.labels(org_id=str(org.id), is_public=str(mock.is_public)).inc()
```
`monitoring/grafana/dashboards/business.json` SLO panel'lari bilan qo'shish.

**Production reference**: Stripe'da M3DB'da 50K+ custom metrics. Qoida: har muhim biznes event = metrika.

### 6.3 `LeaderboardSnapshot.entries` JSONField yomon scale qiladi

**Fayllar**: `apps/engagement/models.py:LeaderboardSnapshot` — `entries: JSONField` har scope uchun top-1000 foydalanuvchi saqlaydi.

**Scale'da**:
- 100 org × haftalik × 1000 entry × 50 bayt = haftada 5MB snapshot qator
- 5 yil × 52 hafta = faqat engagement_leaderboardsnapshot jadvali uchun 1.3GB
- "X foydalanuvchining Y haftadagi rank'i qancha edi?" so'rovi JSON parsing talab qiladi — index'dan foydalanib bo'lmaydi

**Fix**: `LeaderboardEntry(snapshot_id, user_id, rank, score)` jadvaliga normalizatsiya. `(snapshot_id, user_id)` va `(snapshot_id, rank)` bo'yicha index.

---

## 7. 🟡 Tight Coupling va yomon abstraksiyalar

### 7.1 `exams ↔ catalog` 3 ta FK orqali QuestionVersion'da bog'langan — schema o'zgarishi blast radius'i katta

**Fayllar**: `MockExamQuestion.question_version`, `UserAnswer.question_version`, `QuestionDispute.question_version` barchasi `catalog.QuestionVersion`'ga FK. Har catalog schema o'zgarishi exam team koordinatsiyasini talab qiladi.

**Fix**: Exam'larda `QuestionVersionSnapshot` value object joriy etish (exam.publish vaqtida denormalizatsiya). Exam'lar catalog'ga FK qilmaydi — ular o'z snapshot'iga ega.

**Production reference**: Stripe `Charge.payment_method_details` charge vaqtida denormalizatsiyalangan snapshot. `PaymentMethod` obyekti evolyutsiyalanadi, lekin charge'lar immutable qoladi.

### 7.2 `try/except` ichidagi lazy import'lar circular dependency riskini yashiradi

**Fayllar**: `apps/engagement/streak_service.py:65` — `from apps.commerce import wallet_service` funksiya ichida. `apps/engagement/leagues_service.py:127` — bir xil pattern.

**Hid**: `try/except` ichidagi lazy import = "biz bilamiz bu fail bo'lishi mumkin, faqat fail bo'lmasligiga umid qilamiz". Haqiqiy fix: `RewardService` interface'ni ajratish, interface'ga bog'lanish, implementation'ga emas.

---

## 8. 🟠 Ma'lumotlar bazasi dizayn kamchiliklari

### 8.1 Umumiy access pattern'lar uchun composite index'lar yetishmaydi

2.1-bo'limda batafsil. Quyidagilar uchun migration qo'shish:
- `Notification(user, status, -created_at)` — foydalanuvchining unread feed'i
- `WalletTransaction(wallet, kind, -created_at)` — filtered transaction history
- `ExamAttempt(exam, status, -started_at)` — vaqt bo'yicha leaderboard
- `ExamEvent(organization, user, subject_id, -completed_at)` — user→subject analytics

### 8.2 Status flag'lar uchun partial index yo'q

**Masalan** `OrganizationSubscription` jadvali abadiy o'sadi, lekin 90% so'rov `WHERE status IN ('active', 'trialing') AND auto_renew=true` xohlaydi. Partial index:
```sql
CREATE INDEX subs_active_renewable ON commerce_organizationsubscription (current_period_ends_at)
WHERE status IN ('active','trialing') AND auto_renew = true;
```
90%+ index hajmini tejaydi, tezroq Celery `auto_renew` skan.

### 8.3 `created_by`/`updated_by` audit field'lari yo'q

**Fayllar**: Faqat `Organization.created_by` mavjud. `WalletTransaction.created_by` yo'q (kim spend'ni qo'zg'atgan?), `Question.last_edited_by` yo'q.

**Compliance**: SOC2 "kim nimani qachon o'zgartirgan" talab qiladi. Audit mixin yoki `django-simple-history` qo'shish.

---

## 9. 🟠 API dizayn nomuvofiqliklari

| Nomuvofiqlik | Ta'sir |
|---|---|
| `/api/leaderboard/` aslida leaderboard + engagement + search'ni o'z ichiga oladi (concerns aralash) | Mental model chalkashligi |
| HTMX partial'lar uchun `/api/forms/...` VA API uchun `/api/auth/...` | Ikki parallel ierarxiya |
| `/api/auth/otp/send/` VA `/api/auth/otp/send/htmx/` (duplikat) | Maintenance og'irligi |
| Response shape'lari: `{count, results}` vs `{count, transactions}` vs bare list | Frontend'da 3 ta unmarshal yo'li |
| Error envelope'lar: DRF default `{detail: ...}` vs RateLimit `{error, message, tier, retry_after}` vs HTMX HTML | Yagona error handler yo'q |
| Submit/answer'da `question_version` field nomi, ba'zi serializer'larda `question_version_id` (Lesson 15) | Per-endpoint discovery |

**Fix sprint**:
1. Barcha REST API'larni `/api/v1/<domain>/` ostiga ulash
2. HTMX view'larni `/web/<domain>/`'ga ko'chirish
3. Response envelope'ni standartlash: `{success: bool, data: any, error: {code, message, details}, meta: {pagination}}`
4. `drf-spectacular` qo'shish — schema contract bo'ladi

---

## 10. 🟠 Yetishmayotgan enterprise-darajadagi features

| Feature | Holat | B2B ta'siri |
|---|---|---|
| **Audit log** (kim, nima, qachon, oldin, keyin) | ❌ Yo'q | SOC2 / ISO 27001 blocker |
| **SSO / SAML** | ❌ Faqat Google/Yandex/Apple OAuth | B2B "biz Okta ishlatamiz" deal blocker |
| **Per-tenant data residency** | ❌ Bitta DB | EU GDPR mijozlari faqat EU data talab qiladi |
| **Per-tenant rate limits** | ❌ Faqat global tier'lar | Scale'da fair-share imkonsiz |
| **Webhook tizimi B2B uchun** (org-out webhook'lar) | ❌ Yo'q | Integratsiyalar har safar custom dev talab qiladi |
| **Bulk API operatsiyalari** (batch create questions, batch import users) | ❌ Yo'q | Maktab onboarding 10K student = 10K REST chaqiruv |
| **API token'lar (long-lived, scoped)** | ❌ Faqat Cookie JWT | Server-to-server integratsiyalar imkonsiz |
| **Field-level permission'lar** | ❌ Faqat model-level RBAC | O'qituvchi student'ning PII'ni keraksiz ko'radi |

Bular **enterprise uchun table-stakes** ($50K+/yil contract).

---

## 11. 🟠 Dynamic configuration cheklovlari

### 3.1-bo'limda yopildi — magic constant'lar + per-tenant override yo'q.

Qo'shimcha bo'shliq: **env boolean'lardan tashqari feature flag tizimi yo'q**. Quyidagilarni qila olmaysiz:
- Feature'ni 5% foydalanuvchilarga yuborish
- Aniq bir org uchun AI tutor'ni o'chirish (agar ularning byudjeti tugagan bo'lsa)
- Streak milestone threshold'larini A/B test qilish

**Fix**: `django-flags` (bepul) yoki LaunchDarkly (pullik, 100K MAU'da ~$3000/oy) qabul qilish.

---

## 12. 🟠 DevOps va deployment risklari

### 12.1 Haqiqiy cluster yo'q — barcha manifest, deploy validatsiyasi yo'q

**Fayllar**: `charts/yuzdanyuz/`, `argocd/`, `terraform/cloudflare/` — barchasi yozilgan, lekin Task 10 rejada foydalanuvchi "Hozircha skip — faqat manifests yoziladi" dedi.

**Birinchi real deploy'da risk**: Sinovdan o'tmagan manifest'lar = quyidagilarning yuqori ehtimoli:
- HPA scaling thrashing (CPU target 70% Django gunicorn uchun juda past)
- NetworkPolicy PHP-FPM watchdog'ni bloklash (hozirgi self-hosted setup buziladi)
- WAL-G CronJob jim fail bo'ladi (backup-not-run uchun alert yo'q)

**Mitigation**: Hozirgi serverda bitta nodali `k3s` ko'tarish, kamaytirilgan replica bilan chart'ni deploy qilish, synthetic load test o'tkazish. Cluster uchun pul to'lashdan oldin muammolarni topish.

### 12.2 PHP-FPM watchdog ajoyib, lekin production-grade sifatida hujjatlanmagan

**Fayllar**: `_yuzdanyuz_keepalive.php` + `private/yuzdanyuz/watchdog.sh` + `MEMORY.md:yuzdanyuz_persistent_backend.md`

**Risk**: Bu hozirgi production lifecycle yechimi. Aqlli, lekin:
- Watchdog log'lari `/tmp`'da (reboot'da tozalanadi, log aggregation yo'q)
- Token rotation manual (bitta hardcoded `FpaTnY7nmWWZKM7dLiGcCXFxNpbpEGLM`)
- Watchdog'ning o'ziga monitoring yo'q (`{watchdog dead AND backend dead} = jim`)

**Fix**: Log'larni `/home/hsm/logs/yuzdanyuz/`'ga ko'chirish, bir xil Sentry/Prometheus bilan integratsiya, oylik token rotation, tashqi healthcheck qo'shish.

### 12.3 Pre-commit + pre-push hook'lar lokal venv state'ga tayanadi — yana yolg'on gapiradi (Lesson 17)

**Fix**: Allaqachon lesson'da kuzatilgan. Pre-push hook CI'ni aks ettirish uchun yangi venv'ga `pip install -r requirements/base.txt -r requirements/dev.txt` qilishi kerak (yoki `nox` / `tox` ishlatish).

---

## 13. 🟠 Multi-tenant tayyorgarlik muammolari

### Allaqachon batafsil:
- RLS qoplami 28% (2.4-bo'lim)
- Tenant-overridable config yo'q (3.1-bo'lim)
- Per-tenant rate limit yo'q (10-bo'lim)
- Per-tenant data residency imkonsiz (10-bo'lim)

### Qo'shimcha topilma:
**`TenantManager` `unscoped_context()` opt-in, lekin admin/Celery kod uni izchil ishlatmaydi.** Risk: Background task'da context'ni belgilashni unutadi → birinchi org'ning data'sida ishlaydi (TenantManager fail-closed o'rniga `.none()` qaytaradi — jim ko'rinmaydigan data). Har Celery task explicit context bilan boshlanishini tasdiqlovchi `pytest` marker qo'shish.

---

## 14. 🟡 Observability/logging/monitoring bo'shliqlari

| Bo'shliq | Severity | Fix vaqti |
|---|---|---|
| Business metrics yo'q (6.2-bo'lim) | 🟠 | 1 sprint |
| Distributed tracing custom span yo'q | 🟡 | 1 hafta |
| Log'lar unstructured (string interp, JSON key emas) | 🟡 | 2 kun |
| Readiness probe Anthropic/Telegram/Payme'ni tekshirmaydi | 🟡 | 1 kun |
| SLO/SLI ta'riflari yo'q | 🟠 | 1 sprint |
| Sentry release tracking + deploy marker yo'q | 🟡 | 2 soat |
| Log retention policy yo'q (log'lar abadiy o'sadi) | 🟡 | 1 kun |

**Production SLO misoli**:
```
yz-exams-submit-p99 < 1s (28-kunlik oynaning 99.5%'i)
yz-leaderboard-update-lag-p95 < 5s
yz-payment-webhook-success > 99.9%
```

---

## 15. 🟢 Caching imkoniyatlari

6.1-bo'limda yopildi. Qo'shish:
- ORM-darajadagi cache uchun `cachalot` (kam kuch, katta foyda — read-heavy uchun)
- `/api/exams/mocks/` listing'da HTTP cache header (`Cache-Control: max-age=300`)
- `/sitemap.xml`, `/api/leaderboard/global/?period=weekly` uchun Edge cache (Cloudflare) (TTL 60s)

---

## 16. 🟠 Queue/background job yaxshilashlar

Allaqachon yopildi:
- Bir nechta queue (2.2-bo'lim)
- Beat'da distributed lock (2.3-bo'lim)
- Idempotency guard'lar (broken streaks, finalize_score)

### Qo'shimcha:
**Dead-letter queue yo'q.** Agar `evaluate_openended_submission` 3 marta fail bo'lsa, task shunchaki... yo'qoladi. Qo'shish:
```python
@shared_task(autoretry_for=(Exception,), max_retries=3, retry_backoff=True,
             on_failure=lambda *a: deadletter_queue.push(*a))
def evaluate_openended_submission(self, submission_id):
    ...
```
DLQ admin UI orqali reprocess-via-admin'li `FailedTask` modeliga aylanadi.

---

## 17. 🟢 Event-driven arxitektura imkoniyatlari

1.1-bo'limda yopildi.

### Qo'shimcha: `Debezium` orqali **CDC (Change Data Capture)** imkoniyati → Kafka.
- ExamAttempt status o'zgarishi → avtomat event sifatida publish qilinadi
- Kodda explicit `event.publish()` shart emas
- Multi-region replication bepul

Lekin bu Q4 ishi, darhol emas.

---

## 18. 🟢 Plugin/modular arxitektura imkoniyatlari

3.2-bo'limda yopildi. Eng yuqori-qiymatli plugin'lar:
1. **Payment provider** (3 implementation: hozir Payme/Click stub, yaqinda real, keyinroq xalqaro Stripe)
2. **SMS backend** (allaqachon `SMS_BACKEND` env orqali pluggable, lekin entrypoint-based emas)
3. **AI provider** (hozir Anthropic, fallback/cost optimization uchun OpenAI / Gemini)
4. **Anti-cheat strategy** (per enterprise — strict vs lenient)

---

## 19. 🟡 Xarajat optimallashtirish imkoniyatlari

| Lever | Tejov | Kuch |
|---|---|---|
| `analytics.ExamEvent`'ni ClickHouse'ga ko'chirish (allaqachon stub-toggled) | -70% PG storage | 1 sprint |
| `/sitemap.xml`, leaderboard listing'larda aggressive HTTP cache (Cloudflare edge) | -30% backend RPS | 1 kun |
| AI tutor system prompt'lar uchun Anthropic cache `cache_control: ephemeral` ishlatish | -30% Anthropic xarajat | 2 soat |
| Steady-state DB/Redis uchun Reserved Instances / Savings Plans | -40% AWS/Hetzner | 1 kun qaror |
| Tighter Celery worker autoscaling (hozirgi: always-on, scale-to-zero yo'q) | -50% off-hours compute | 1 sprint KEDA |
| `select_related`/`prefetch_related` (2.1-bo'lim) | -60% DB CPU → upgrade'ni kechiktirish | 1 sprint |

100K DAU'da kumulativ tejov: $3-8K/oy.

---

## 20. 🟠 Texnik debt risklari

### Yuqori-priority debt itemlari, tartiblangan:

| # | Item | To'lash vaqti | To'lamaslik narxi |
|---|---|---|---|
| 1 | N+1 audit + fix | 1 sprint | 10K DAU'da DB upgrade shoshilinch |
| 2 | Soft-delete + GDPR tayyorgarligi | 2 sprint | EU launch bloklangan, tartibga solish jarimasi |
| 3 | Celery queue separatsiya + distributed lock | 1 sprint | Partial failover paytida outage |
| 4 | drf-spectacular + API v1 split | 2 sprint | Mobile jamoa cheksiz bloklangan |
| 5 | FSM library qabul qilish | 1 sprint | Race condition bug'lar to'planib boradi |
| 6 | RLS qolgan 5 app'ga | 1 sprint | L3 defense yarim qurilgan |
| 7 | Soft-delete audit `User → Wallet/Tx`'da | 1 sprint | Bitta test foydalanuvchini xavfsiz o'chirib bo'lmaydi |
| 8 | Business metrics (Prometheus custom) | 1 sprint | SRE ko'r holatda uchadi |
| 9 | Per-tenant config | 2 sprint | B2B enterprise sales bloklangan |
| 10 | Audit log + SOC2 prep | 2 sprint | Enterprise contract'lar bloklangan |

---

# 📊 Priority Matrix (Senior Architect Roadmap)

## Sprint 1 (BU HAFTA — qonayotgan jarohatlarni tuzatish)
- [ ] **N+1 sweep** — har joyda `select_related`/`prefetch_related` qo'shish; CI'da `nplusone` strict
- [ ] **Beat distributed lock** — `check_broken_streaks` + `leagues_weekly_recalc`'da Redis lock (arzon fix)
- [ ] **Soft-delete shim** `WalletTransaction`, `ExamEvent`, `OpenEndedSubmission`'da (CASCADE o'rniga `is_deleted` mark qilish — back-compatible)
- [ ] **Custom Prometheus metrics** — kamida 5 ta asosiy counter (exams_submitted, payments_completed, ai_tutor_calls, leaderboard_updates, sms_sent)
- [ ] **External readiness check'lar** — `/health/ready/`'da Anthropic + Redis + PG

**Kuch**: 1 senior engineer × 1 hafta.

## Sprint 2-3 (BU OY — frontend'ni unblock qilish)
- [ ] `drf-spectacular` qo'shish — OpenAPI auto-doc
- [ ] HTMX view'larni `apps/<app>/views/htmx.py`'ga, REST'ni `apps/<app>/views/api.py`'ga ko'chirish
- [ ] REST'ni `/api/v1/<domain>/` ostiga ulash, HTMX `/api/...` path'larini deprecate qilish
- [ ] Response envelope'ni standartlash
- [ ] 7 ta state-machine modeli uchun `django-fsm-2`'ni qabul qilish

## Q2 (KEYINGI 3 OY — scalability)
- [ ] Celery queue separatsiya (`critical` / `ai` / `batch` / `realtime`)
- [ ] Queue depth'da KEDA autoscaling
- [ ] Qolgan 5 app uchun RLS migration (engagement, commerce, intelligence, analytics, organizations)
- [ ] `LeaderboardSnapshot.entries` → normalized `LeaderboardEntry` jadval
- [ ] Service-layer unit test'lar (maqsad ≥80%)

## Q3 (6 OY — enterprise tayyorgarligi)
- [ ] Audit log (`django-simple-history` yoki custom)
- [ ] Soft-delete + GDPR erasure endpoint
- [ ] SAML SSO (saml2 lib)
- [ ] Per-tenant config UI (Organization.settings)
- [ ] B2B uchun Webhook tizimi (signing bilan org-out webhook'lar)
- [ ] Bulk API operatsiyalari
- [ ] Scope bilan long-lived API token'lar

## Q4 (12 OY — haqiqiy production-grade)
- [ ] Analytics'ni real ClickHouse'ga ko'chirish (allaqachon stubbed)
- [ ] Multi-region uchun CDC pipeline (Debezium → Kafka)
- [ ] Celery beat'ni `temporal.io` yoki KEDA ScaledJob'lar bilan almashtirish
- [ ] Multi-region active-passive (EU'da read replica)
- [ ] LaunchDarkly feature flag'lar
- [ ] SOC2 Type 1 audit

---

# 🎯 Achchiq haqiqatli yakuniy baholash

**Nima ajoyib bajarilgan** (tuzatmang, nishonlang):
1. **Tenant isolation defense-in-depth dizayni** (L1-L5 stack) — men ko'rgan B2B SaaS'larning 80%'idan yaxshi
2. **Har wallet operatsiyasida `select_for_update`** — pulemyot-himoyalangan
3. **Tashqi servislar uchun stub-mode pattern** — dev tezligi uchun best-practice
4. **511-test pytest suite + pre-push hook** — Python SaaS'lar orasida top decile
5. **CLAUDE.md + LESSONS.md madaniyati** — jamoa o'zgarsa ham yashaydigan institutional o'rganish
6. **PHP-FPM watchdog innovation** — HestiaCP jail cheklovi uchun ijodiy yechim

**Hozirgicha davom etsangiz nima risk ostida**:
- 10K DAU'ga yetish N+1'ni katastrofik tarzda ochib beradi
- Beat pod'ning birinchi haqiqiy failover'i = SMS bill skandali
- Birinchi EU mijoz GDPR erasure talab qiladi → refactor'siz imkonsiz
- Birinchi mobile app major release → backend contract noaniqligi
- Birinchi "biz Okta ishlatamiz" enterprise deal → bloklangan

**12 oy ichida agar Sprint 1-3 rejasini bajarsangiz, bu codebase qanday ko'rinadi**:
Enterprise mijozlarga ishonchli pitch qila oladigan va Hacker News frontpage spike'idan omon qoladigan haqiqiy production-grade B2B SaaS. Arxitektura asosi yaxshi; sistemali fix'lar mexanik, chuqur redesign emas.

**Agar bajarmasangiz, 12 oy ichida qanday ko'rinadi**:
Quarterly board meeting paytida outage chunki beat double-fired notification. Pager rotation. Tech debt sprint sikllari. Yangi engineer'lar qaysi `views.py` fayl nimani handle qilishini tushunmaganligi sababli ishga olish ishlamaydi. CTO endi rewrite $200K emas, $2M ekanini tushunadi.

---

**Tavsiya**: Sprint 1 itemlari (5 ta fix, 1 hafta) production qonashning 80%'ini ushlaydi. Avval shularni qiling, keyin har oy priority'larni qayta ko'rib chiqing.

---

> Hujjat 2026-05-16 sanasida Claude Opus 4.7 (1M context) tomonidan tayyorlangan, 4 ta parallel Explore sub-agent tomonidan deep code map sintezi asosida.
