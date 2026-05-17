# LESSONS.md — Self-Improvement Log

> Patterns learned from mistakes. Read at session start. Add after every user correction.

**Format**: `### Lesson NN — Title` then "Mistake → Why → Fix → Pattern"

---

## Lesson 01 — PostgreSQL: SET app.X = NULL is invalid

**Mistake**: RLSMiddleware used `SET app.current_org_id = NULL;` for anonymous users.

**Why it broke**: PostgreSQL doesn't accept NULL as a session variable value via `SET` (different from `=` in queries). Triggers `syntax error at or near "NULL"`.

**Fix**: Use `RESET app.current_org_id;` to clear, not `SET ... = NULL`.

**Pattern**: When clearing PostgreSQL session variables, always use `RESET <name>`. Reserve `SET <name> = '<value>'` only for actual values.

**Caught by**: pre-push hook (Pytest security tests).

---

## Lesson 02 — Django + django-celery-beat version compatibility

**Mistake**: `requirements/base.txt` pinned `django-celery-beat==2.7.0` while using `Django==5.2.14`. Pip install failed in CI with `ResolutionImpossible`.

**Why**: `django-celery-beat 2.7.0` requires `Django<5.2`. Local venv had it pre-installed so didn't notice.

**Fix**: Bump to `django-celery-beat==2.8.1` (Django 5.2 support).

**Pattern**: Before bumping Django minor version, audit ALL `django-*` dependencies for compatibility. CI's fresh `pip install` catches what local venvs hide.

**Caught by**: GitHub Actions backend job (fresh env).

---

## Lesson 03 — conftest.py imports must be in dev requirements

**Mistake**: `conftest.py` had `import fakeredis` but `fakeredis` was missing from `requirements/dev.txt`. CI failed with `ModuleNotFoundError`.

**Why**: Local venv had it manually installed.

**Fix**: Added `fakeredis==2.35.1` to `requirements/dev.txt`. Same for `pytest-cov`.

**Pattern**: Every import in `conftest.py`, `tests/`, `apps/*/tests.py` MUST be declared in `requirements/dev.txt`. Local venvs lie.

**Caught by**: CI backend job.

---

## Lesson 04 — TypeScript path aliases need both baseUrl + paths

**Mistake**: `tsconfig.json` had `"paths": { "@/*": ["./src/*"] }` but no `"baseUrl": "."`. Imports like `@/services/auth` failed with "Cannot find module".

**Fix**: Added `"baseUrl": "."` alongside `paths`.

**Pattern**: Next.js path aliases need BOTH `baseUrl` (relative reference point) AND `paths` (alias mapping). Either alone is incomplete.

**Caught by**: CI frontend-web job (`tsc --noEmit`).

---

## Lesson 05 — Pre-push hooks catch real bugs

**Mistake**: Hooks felt like overhead, considered `--no-verify` for speed.

**Why we didn't**: Pre-push hook caught Lesson 01 (RLS NULL bug) before it reached CI.

**Pattern**: NEVER `--no-verify` unless explicitly justified. Hooks are slower but they catch bugs in your time, not CI's. The 30-60 sec local cost saves 5-10 min CI cycle + failure shame.

**Caught by**: Lesson 01 retrospective.

---

## Lesson 06 — localhost vs 127.0.0.1 on shared servers

**Mistake**: Set `DB_HOST=localhost` in `.env` on `srvr1.sammu.uz`. Connection failed with "Connection refused" pointing to public IP.

**Why**: On this server, `localhost` resolves to the public IP `109.94.172.117`, not `127.0.0.1`. PostgreSQL listens only on 127.0.0.1.

**Fix**: Use `DB_HOST=127.0.0.1` explicitly.

**Pattern**: On shared/cPanel servers, never trust `localhost` to mean loopback. Always use `127.0.0.1` for local services.

---

## Lesson 07 — VS Code git auto-detection picks up cache repos

**Mistake**: VS Code Source Control panel showed pre-commit framework's hook clones (`~/.cache/pre-commit/repo*/`) as if they were our repos.

**Fix**: `.vscode/settings.json` → `"git.autoRepositoryDetection": "openEditors"` + `"git.detectSubmodules": false`.

**Pattern**: For pre-commit + monorepo workflows, restrict VS Code git detection to `openEditors` to avoid noise from `.cache/` clones.

---

## Lesson 08 — Django model `Meta` must come BEFORE `__str__`

**Mistake**: Added `__str__` before `Meta` class in `QuestionDraft`. Ruff DJ012 caught it.

**Pattern**: Django Style Guide order:
1. Field declarations
2. `Meta` class
3. `__str__` method
4. Other custom methods

---

## Lesson 09 — pre-commit JSON check fails on JSONC

**Mistake**: Added `.vscode/settings.json` with `// comments`. Pre-commit `check-json` rejected it (not valid JSON).

**Fix**: `.pre-commit-config.yaml` → `check-json` exclude `^\.vscode/` (VS Code uses JSONC).

**Pattern**: When configs allow comments (`tsconfig.json`, `.vscode/*.json`), exclude them from strict JSON checkers.

---

## Lesson 10 — Dependabot PRs created from broken main = stale forever

**Mistake**: 17 Dependabot PRs failed CI because main was broken when they were branched. Dependabot doesn't auto-rebase open PRs after main fixes.

**Fix**: Closed all stale PRs. Dependabot recreates next cycle from clean main.

**Pattern**: After fixing main breakage, audit Dependabot PRs. Either close + let recreate, OR comment `@dependabot rebase` on each.

---

## Lesson 11 — Middleware-set request attributes need integration tests

**Mistake**: `RLSMiddleware` `request.org`'ni o'qigan, lekin **hech bir middleware uni set qilmagan**. `TenantMiddleware` faqat thread-local'ga `set_current_org()` chaqirgan. Natija: PostgreSQL RLS qatlami (L3 defense) **butunlay ishlamagan** — har doim `RESET app.current_org_id` chaqirilgan. 6 ta integration/security test mavjud edi, barchasi `set_current_org()` yoki `tenant_context()` orqali manual context o'rnatib, middleware zanjirini bypass qilgan.

**Why it broke**: Unit testlar middleware'ning bir qismini izolyatsiyada tekshiradi. `test_auth_flows.py` Django `Client` bilan to'liq zanjirni ishlatadi, ammo tenant isolation'ni assert qilmaydi (faqat status code'larni). Hech bir test **request lifecycle butun zanjirini** ushlamagan.

**Fix**:
1. `TenantMiddleware.__call__`'da `request.org = org` qo'shish
2. JWT `verify_exp=True` (defense-in-depth)
3. `TenantManager` fail-closed (`org=None` → `.none()`) + `unscoped_context()` helper
4. `RLSMiddleware` fail-closed (xato bo'lsa 500, swallow qilmasin)
5. End-to-end test (`tests/integration/test_middleware_chain_e2e.py`) — Django Client orqali butun zanjirni ishlatib, response'da `request.org` va PostgreSQL `app.current_org_id` qiymatini assert qiladi

**Pattern**: Har bir `request.X` attribute uchun **end-to-end test** kerak — Django `Client` orqali real HTTP request yuborib, view ichida (yoki test echo middleware'da) attribute mavjudligi va to'g'ri qiymatga egaligi assert qilinishi shart. Manual context setup (`set_current_org`, `tenant_context`) faqat **birlik testlari** uchun, middleware zanjiri uchun emas.

**Caught by**: Manual code review (CLAUDE.md "Defense-in-Depth" audit, 2026-05-16).

---

## Lesson 12 — Auth API view'lar HTMX-style form-encoded ishlaydi, JSON emas

**Mistake**: E2E test'da `POST /api/auth/email/` `Content-Type: application/json` bilan `{"email":"...","password":"..."}` yuborildi va `400 "Email/foydalanuvchi nomi yoki parol noto'g'ri"` kelaverdi.

**Why it broke**: `EmailLoginView`, `RegisterView` va boshqa accounts/views/login_views.py'dagi view'lar HTMX dispatcher uchun ishlab chiqilgan — ular `request.POST.get(...)` ishlatadi (form-encoded), `request.data` (DRF JSON) emas. Bundan tashqari:
1. Login field nomi `login` (username yoki email qabul qiladi), `email` emas
2. Register'da `password_confirm` majburiy (password2/confirm_password emas)
3. `CsrfViewMiddleware` API endpoint'larda ham faol — `X-CSRFToken` header + `Referer` kerak
4. Email verification — register'dan keyin login HTTP 403 ("Email tasdiqlanmagan") chunki allauth `EmailAddress.verified=False`. Test uchun superuser yoki email_addr.verified=True qilingan user kerak

**Fix** (E2E test sinov uchun): form-encoded, `login` field, CSRF token GET `/admin/login/` orqali olish:
```bash
curl -c $COOKIE -b $COOKIE -X POST $BASE/api/auth/email/ \
  -H "X-CSRFToken: $CSRF" -H "Referer: $BASE/" \
  --data-urlencode "login=narzullayevme" \
  --data-urlencode "password=$PASS"
```

**Pattern**: Frontend Next.js'dan API chaqirilganda — `fetch('/api/auth/email/', { body: new URLSearchParams({login, password}) })` bilan form-encoded yuborish kerak. Mobile RN — `qs.stringify`. DRF JSON emas, HTMX style.

**Caught by**: E2E manual smoke test (2026-05-16), lokal AI agent diagnostikasi.

---

## Lesson 13 — RateLimitMiddleware 60s window, sinov chog'ida pause kerak

**Mistake**: E2E test 5-6 ta consecutive auth urinishlar yubordi (CSRF/parol topish ketma-ketligi), 7-chi urinishdan boshlab barcha API endpoint'lar `HTTP 429 retry_after: 60` qaytardi — testlar to'xtab qoldi.

**Why it broke**: `core.middleware.rate_limit.RateLimitMiddleware` Redis-backed sliding window — API tier'da auth endpoint'lariga juda ko'p so'rovlar 60s ichida bloklanadi. Bu defense-in-depth — production'da brute-force himoyasi, lekin dev test'da yo'lda turadi.

**Fix**:
1. Sinov scriptlarida `sleep 1` har consecutive request'da
2. Yoki `dev.py`'da rate limit'ni disable qilish: `RATE_LIMIT_ENABLED=False`
3. Yoki Redis'da test boshida `redis-cli flushdb` (DB=1)

**Pattern**: API test scriptlari rate limit'ni hisobga olishi shart. Iloji bo'lsa dev/test settings'da off, yoki har request'lar orasida pause.

**Caught by**: E2E test (2026-05-16) — 7-chi consecutive request 429 berdi.

---

## Lesson 14 — DRF API javoblari nested struktura — jq parsing aniq bo'lishi kerak

**Mistake**: `POST /api/exams/mocks/<id>/start/` javobini `jq -r '.id'` bilan parse qilindi — null qaytdi, keyingi `/answer/` request URL'da bo'sh attempt_id bilan `404` kelaverdi.

**Why it broke**: Start endpoint nested response qaytaradi:
```json
{ "attempt": { "id": "...", "status": "in_progress" }, "exam": { "questions": [...] } }
```
ID `.attempt.id`'da, `.id`'da emas.

**Fix**: `jq -r '.attempt.id'` aniq path bilan. Yoki avval `echo $RESP | jq .` bilan struktura tekshirish.

**Pattern**: Yangi endpoint chaqirilganda, birinchi response'ni to'liq `jq .` bilan ko'rish — keyin aniq path bilan parsing.

**Caught by**: E2E test (2026-05-16) — attempt_id bo'sh chiqdi.

---

## Lesson 15 — DRF PrimaryKeyRelatedField raw field name (yo'q `_id` suffix)

**Mistake**: E2E test scripti `POST /api/exams/attempts/<id>/answer/` ga `{"question_version_id": "<uuid>", "selected": [2]}` yubordi. HTTP 400 keldi:
```json
{"question_version": ["Ushbu maydon to'ldirilishi shart."]}
```

**Why it broke**: `apps/exams/views.py` `SubmitAnswerSerializer` field nomi `question_version` (FK), `_id` suffix qo'shilmaydi:
```python
class SubmitAnswerSerializer(serializers.Serializer):
    question_version = serializers.PrimaryKeyRelatedField(queryset=QuestionVersion.objects.all())
    selected = serializers.ListField(child=serializers.IntegerField())
```
Django ORM'da `question_version_id` field bor (DB column), lekin DRF serializer FK field'ini `question_version` deb ataydi. Klassik django/DRF inconsistency.

**Fix**: `{"question_version": "<uuid>", "selected": [N]}` — pure key, `_id` qo'shmasdan.

**Pattern**:
- DRF serializer field nomi — `Serializer` class'idagi atribut nomi
- Django ORM model field — DB column nomi (FK uchun `<name>_id`)
- API client kod yozishda — har doim serializer class'ini ko'rib chiqish, ORM'dan kelib chiqmaslik
- Yoki swagger/redoc joriy qilish (drf-spectacular) — bu nuance dokumentlashda yo'q

**Caught by**: E2E test (2026-05-16) — lokal AI agent diagnozi, view source'dan serializer class'ni o'qidi.

---

## Lesson 16 — `.env` orqali kelgan kalit settings'da export qilinmagan bo'lsa, `settings.X` bo'sh

**Mistake**: `.env`'da `ANTHROPIC_API_KEY=sk-ant-...` (108 char) yozilgan, lekin `apps/intelligence/tasks.py:40` `getattr(settings, 'ANTHROPIC_API_KEY', '')` ishlatardi va bo'sh string qaytardi → "ANTHROPIC_API_KEY settings/env'da yo'q" deb fail bo'lardi.

**Why it broke**: `core/settings/base.py`'ga `ANTHROPIC_API_KEY = os.getenv(...)` qatorini qo'shish unutilgan. `load_dotenv()` env-var sifatida yuklaydi, lekin `settings.X` deb murojaat qilish uchun aniq Python qator kerak. `os.environ['ANTHROPIC_API_KEY']` ishlaydi (env-var), `settings.ANTHROPIC_API_KEY` ishlamaydi.

**Fix** (`core/settings/base.py`):
```python
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL   = os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-4-5')
```

Keyin Celery worker'ni restart (settings cache).

**Pattern**:
1. `.env`'ga qator qo'shish → settings.py'ga ham qator qo'shish kerak (yoki `os.environ.get()` ishlatish service ichida)
2. Yangi setting qo'shilganida — har bir env'da (dev/staging/prod) export qilingani tekshirish
3. **Verification**: `python -c "from django.conf import settings; print(settings.MY_KEY)"` bilan tasdiqlash
4. **Test**: integration test'ida `@override_settings(MY_KEY='test-value')` o'rniga env-var orqali real flow tekshirish
5. **Service code**: `getattr(settings, 'KEY', '')` pattern xavfli — silently '' qaytaradi. Yo `settings.KEY` (KeyError raise) yoki bo'sh tekshiruv majburiy

**Caught by**: E2E test (2026-05-16) — S7 tutor.generate task fail bo'ldi, eager run lokal jarayonda ishladi (yangi settings load), lekin Celery worker (eski settings cache) fail edi.

---

## Lesson 17 — settings/base.py MIDDLEWARE'ga import qo'shsang, requirements/base.txt'ga ham qo'sh

**Mistake**: PR #39'da `core/settings/base.py` MIDDLEWARE ro'yxatiga `'whitenoise.middleware.WhiteNoiseMiddleware'` qo'shildi, lekin `whitenoise==6.9.0` faqat `requirements/prod.txt`'da edi. CI test job `dev.txt` o'rnatadi → whitenoise yo'q → ASGI handler init paytida ImportError:
```
ModuleNotFoundError: No module named 'whitenoise'
ERROR collecting tests/integration/test_exam_websocket.py
ERROR collecting tests/integration/test_leaderboard_websocket.py
```

Pre-push hook lokal venv'da o'tdi (men `pip install whitenoise` qilib qo'ygan edim manual). Lesson 03'ning aniq qaytarilishi — "local venvs lie".

**Why it broke**: `settings/base.py` butun MIDDLEWARE zanjirini ifodalaydi (test settings ham `from .base import *`). MIDDLEWARE'da ko'rsatilgan har bir module SARTAN import qilinadi `load_middleware()` chaqirig'i paytida. Test env'da pip faqat `dev.txt` o'rnatadi (`-r base.txt` orqali base'ni qamrab oladi, lekin prod.txt'ni emas).

**Fix**: `whitenoise==6.9.0`'ni `base.txt`'ga ko'chirish — middleware sifatida har env'da kerak. `prod.txt`'da kommentariy qoldirish (manbai ko'chgan).

**Pattern**:
1. `base.py` MIDDLEWARE / INSTALLED_APPS / DATABASE ENGINE — har bir paketga `requirements/base.txt`'da nuqta bo'lishi shart
2. `prod.txt`'da faqat **runtime-only** paketlar (gunicorn, sentry, opentelemetry-distro) — ular settings/base.py'da import qilinmaydi, faqat env-conditional kod orqali ishlatiladi
3. **CI vs local test**: GitHub Actions har turin yangi venv yaratadi va faqat `requirements/<env>.txt` ishlatadi → manual `pip install`'lar local venv'da yashiringan dependency'larni qoplaydi. Pre-push hook bu xatoga tushib qoladi
4. **Audit pattern**: yangi import qo'shilganda, `grep -E "^[a-z]" requirements/base.txt`'da paket borligini tekshirish. IDE diagnostika ham yordam beradi ("Package X not installed")

**Caught by**: GitHub Actions CI run #63 va #64 (PR #39 merged'dan keyin ham post-merge CI'da).

---

## Lesson 18 — Pulli external API chaqiruvchi endpoint'lar input type'ni qattiq validate qilishi shart

**Mistake**: `OpenEndedSubmitView` (`apps/intelligence/views.py:117`) `question_version` qabul qilardi va to'g'ridan-to'g'ri AI grading Celery task'ga jo'natardi — savol type'i (single-choice yoki essay) tekshirilmasdi. E2E sinov'da SC savolga essay submit qilindi → Anthropic API real chaqirilgan, ~$0.01 pul ketdi, AI rubrikasi noto'g'ri savol uchun chiqdi.

**Why it broke**: View qabul qiladigan `question_version` har qanday QV bo'lishi mumkin. `Question.Type` (SC, MC, MT, OR, FB, **OE**, **FU**) — faqat oxirgi 2 turi (OPEN_ENDED, FILE_UPLOAD) OpenEnded submission uchun mantiqiy. Boshqa turlar uchun:
- Pul ketadi (Anthropic API call)
- AI noto'g'ri kontekstda baholaydi (rubrika "essay" deb, lekin user faqat option tanlaganini ko'rsatadi)
- Data integrity: SC savolga essay content saqlangan, keyingi analytics noto'g'ri ko'rsatadi

**Fix** (`apps/intelligence/views.py` OpenEndedSubmitView):
```python
from apps.catalog.models import Question

allowed_types = (Question.Type.OPEN_ENDED, Question.Type.FILE_UPLOAD)
if qv.question.type not in allowed_types:
    raise ValidationError({
        'question_version': f"Savol type'i '{qv.question.type}' OpenEnded uchun mos emas. "
                            f"Faqat OE (Essay) yoki FU (File upload) qabul qilinadi."
    })
```

Yangi test: `test_submit_rejects_non_oe_question_type` — SC savol bilan POST → 400, va `OpenEndedSubmission.objects.exists() == False` (DB write ham qilinmaganini tasdiqlash, Anthropic API ham chaqirilmagan).

**Pattern**: External API chaqiruvchi (pulli yoki rate-limited) endpoint'lar uchun:
1. **Input type tasdiqlash** — model `type`, `kind`, `category` field'lari orqali ruxsat etilgan domain'larni cheklash
2. **Negative test mandatory** — har bir validation rule uchun "rejected" test (assertion: 400 + DB yo'q + side-effect yo'q)
3. **Fail-fast before async dispatch** — `evaluate.delay(...)` chaqirig'idan oldin validate qilish, aks holda Celery worker pul ketishini va keyin failure'ni log qiladi
4. **Audit trail** — kim qaysi qv'ga submit qildi, type bilan birga log qilish (debugging uchun)

**Caught by**: E2E test (2026-05-16) — lokal AI agent S13 senariosida SC savolga essay submit qilib 202 va AI rubrika qaytarilganini ko'rdi (kutilgan: 400).

---

## Lesson 19 — `global_objects` recommendation/search flow'larida cross-tenant leak

**Mistake**: `apps/intelligence/services.py:recommend_questions` cold-start va weak-skill matching uchun `Question.global_objects.order_by('?')` ishlatardi. Maqsad — B2C foydalanuvchilarga keng savol oqimi berish edi, lekin natija — autentifikatsiya qilingan har qanday foydalanuvchi (boshqa tenant a'zosi ham) **boshqa tashkilotlarning private question bank'larini** ko'ra olardi.

**Why it broke**: `global_objects` (GlobalManager) tenant filter'ni bypass qiladi — bu admin/superuser yoki Celery worker uchun ataylab yaratilgan. Lekin oddiy user-facing endpoint (`GET /api/intelligence/skills/recommendations/`) ichida ishlatish — defense'ning birinchi qatlamini (TenantManager) butunlay aylanib o'tishni anglatadi. L3 RLS ham yordam bermaydi: Django default DB role table owner — RLS policies `FORCE ROW LEVEL SECURITY` siz bypass bo'ladi.

**Fix**:
```python
from core.tenant import get_current_org

org = get_current_org()
if org is not None:
    base_qs = Question.objects.all()                                  # TenantManager auto-filter
else:
    base_qs = Question.global_objects.filter(banks__is_public=True).distinct()  # public marker fallback
```

Regression test: `tests/security/test_tenant_boundary_extended.py::TestSkillRecommendationsBoundary` — 2 ta org savol yaratadi, tenant context A'da `recommend_questions` chaqiriladi va B'ning savoli **yo'q** ekanligini assert qiladi.

**Pattern**:
1. **`global_objects` faqat 3 ta legitimate use-case'da**: (a) admin/superuser kontekst, (b) Celery worker explicit `unscoped_context()` ichida, (c) cross-tenant audit/management command. User-facing view ichida ishlatish — **dudoq DAR** xato.
2. **`is_public=True` markeri** — cross-tenant disclosure'ni explicit qilish uchun. Default false: yangi resurs hech qachon avtomat ravishda boshqa tenantga ko'rinmaydi.
3. **Boundary test mandatory**: yangi user-facing query yozilganda, **kamida bitta `test_<name>_no_cross_tenant_leak`** test bo'lishi shart — 2 ta org + assertion.
4. **`get_current_org()` check** — tenant-aware service ichida birinchi qator: org yo'q bo'lsa explicit fallback (public-only yoki 403), default qoldirmang.

**Caught by**: 2026-05-17 `tenant-auditor` subagent audit (ISSUE-109 C1).

---

## Lesson 20 — RLS yoqilgan + 0 ta policy = silent breakage future booby-trap

**Mistake**: `apps/catalog/migrations/0003_enable_rls.py` `tables_with_rls` ro'yxatiga `catalog_subject` qo'shilgan, lekin u uchun `CREATE POLICY ...` yozilmagan. Hozir test'lar yashil — chunki Django DB role table owner va PostgreSQL ownerga policy talab qilmaydi. `Subject.objects.all()` ishlaydi, hech kim sezmagan.

**Why it broke**: PostgreSQL RLS semantikasi: `ALTER TABLE ENABLE ROW LEVEL SECURITY` + 0 policy = **deny-by-default** ko'rilmaydi, lekin **faqat owner uchun**. Production'da least-privilege role joriy qilinganda (security guideline tavsiyasi), `app_user@db` rolesi `Subject.objects.all()` chaqirsa, RLS qatlami har row'ni filter qiladi va 0 policy bo'lgani uchun **0 row** qaytaradi. Test mavjud emas, hech qanday integration test bu pattern'ni qoplay olmaydi (DB role o'zgarishi alohida deploy step).

**Fix**:
1. Audit har RLS migration'da `tables_with_rls` ro'yxatining har bandi uchun `CREATE POLICY ...` borligini tasdiqlash. `pg_policies` query bilan verify:
   ```sql
   SELECT relname, relrowsecurity, (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policy_count
   FROM pg_class c WHERE relrowsecurity = true ORDER BY relname;
   ```
2. Yangi migration `0007_fix_subject_rls_and_add_delete_policies.py` — `catalog_subject` global model (organization nullable, platform-wide fan), RLS o'chiriladi (`DISABLE ROW LEVEL SECURITY`).
3. `tests/security/test_tenant_boundary_extended.py::TestSubjectGlobal` — Subject 2 ta org context'da ham ko'rinishini assert qiladi.

**Pattern**:
1. **RLS migration pre-commit hook idea**: har `ENABLE ROW LEVEL SECURITY` chaqirig'idan keyin bir xil migration ichida `CREATE POLICY` bo'lsin (yoki olib tashlanishi tushuntirilsin).
2. **CI verify step**: production-like role bilan `SELECT count(*) FROM <table>` chaqirib 0 emasligini assert qilish (table empty bo'lgan testlarda fixture qator yaratish).
3. **Audit cadence**: har `migration-validator` subagent run'ida — yangi `RunPython` migration'larida `ENABLE/DISABLE/CREATE POLICY` muvozanati tekshirilsin.
4. **Single source of truth**: SQL fayl + management command (`apps/organizations/sql/rls_policies.sql`) Django migration'lar bilan teng tutilmasligi shart — bittasi truth manbai (Django migration), boshqasi yo o'chiriladi yo CI orqali avtomat sync.

**Caught by**: 2026-05-17 `tenant-auditor` subagent + manual `pg_policies` query (ISSUE-109 C3).

---

## Capture Template

When the user corrects you, append:

```markdown
## Lesson NN — Title

**Mistake**: <what I did>
**Why it broke**: <root cause>
**Fix**: <how I corrected>
**Pattern**: <rule for future>
**Caught by**: <test/hook/user/CI>
```

After 5+ lessons in a category, consider promoting to a hook or skill.
