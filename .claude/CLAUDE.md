# Milliy Sertifikat — CLAUDE.md (Project Rules)

> **Stack**: Django 5.2 + Next.js 15 + PostgreSQL 16 (RLS) + Redis 7 + Celery + Telegram Bot
> **Loyiha**: O'zbekiston EdTech Super-App (DTM simulyatori + AI diagnostika + B2B white-label)

---

## ⚡ Workflow Orchestration (Boris Cherny core)

### 1. Plan Mode Default
- ANY non-trivial task (3+ steps) → enter plan mode
- Use plan mode for verification too, not just building
- If sideways: STOP, re-plan immediately

### 2. Subagent Strategy
- Use `migration-validator` for ANY migration safety check
- Use `tenant-auditor` for ANY tenant boundary concern
- Use `test-runner` for smart pytest scope (not the whole suite)
- Use `code-reviewer` (universal) for PR-level reviews

### 3. Self-Improvement Loop
- After ANY user correction → update `.claude/LESSONS.md`
- Read LESSONS.md at session start (auto via SessionStart hook)
- Patterns prevent the same mistake twice

### 4. Verification Before Done
- Tests pass + Django check + ruff + manual UI/API check
- Run `/verify` before claiming "done" (mirrors CI exactly)
- For UI: actually open browser, click through, monitor regressions

### 5. Demand Elegance
- Non-trivial: "is there a more elegant way?"
- Hacky fix: "implement the elegant solution knowing what I know now"
- Skip for trivial — don't over-engineer

### 6. Autonomous Bug Fixing
- Bug report → just fix it. Don't ask for hand-holding.
- CI red → diagnose + fix without prompting

---

## 🏗️ Architecture: Critical Patterns

### Multi-Tenancy (DB-level isolation)
**EVERY tenant model** must inherit `TenantTimestampMixin`:
```python
from core.mixins import TenantTimestampMixin

class Question(TenantTimestampMixin):
    # organization FK auto-added
    # objects = TenantManager (auto-filter by current org)
    # global_objects = GlobalManager (admin-only, bypass)
```

**Tenant Context** (CRITICAL):
```python
# In views: TenantMiddleware sets it from JWT
# In tests/management commands: explicit
from core.tenant import tenant_context
with tenant_context(org):
    Question.objects.all()   # only org's questions
```

**Cross-tenant access** = security incident. RLS catches at DB level (defense-in-depth).

### App Structure (Domain-Driven)
```
apps/
  accounts/      — CustomUser, Region, OTPCode, DeviceSession
  organizations/ — Organization, OrgRole, Membership, OrgInvite
  catalog/       — Question, QuestionBank, Tag, AIProviderConfig
  exams/         — MockExam, PracticeSession, ExamAttempt (Task 4)
  intelligence/  — SkillTag, UserSkillProfile (Task 6)
  commerce/      — Wallet, Transaction, Subscription (Task 7)
  engagement/    — Streak, League, Badge (Task 9)
  analytics/     — ClickHouseEvent, Report (Task 8)
```

**Adding new app**: Use `python manage.py startapp` + add to `INSTALLED_APPS` + create models with `TenantTimestampMixin`.

### RBAC — Two Layers
- **Layer 1** (platform): `is_staff`, `is_superuser` — bypasses tenant filters
- **Layer 2** (org): `OrgRole.permissions` JSONField with wildcards
  ```python
  user.has_org_permission(org, 'exams:create')   # exact
  user.has_org_permission(org, 'exams:*')        # resource wildcard
  user.has_org_permission(org, '*')              # all
  ```

### user_type (derived, not stored)
- `platform_admin` — is_superuser
- `platform_staff` — is_staff
- `b2c` — no active Membership
- else — `membership.role.name` (student, teacher, owner, etc.)

### Defense-in-Depth Security Stack
- **L1**: JWT + Device Fingerprinting (Redis session lock)
- **L2**: RBAC + Tenant Isolation (TenantManager + JWT current_org claim)
- **L3**: PostgreSQL RLS (DB-level — `current_setting('app.current_org_id')::uuid`)
- **L4**: Rate Limiting (Redis sliding-window, progressive penalty)
- **L5**: Content Protection (Watermark + DOM Shuffle)

---

## 🚨 Critical Pre-flight Checks

### Before Adding a Model
1. ✅ Inherit `TenantTimestampMixin` (unless intentionally global, like Tag)
2. ✅ Add `__str__` method
3. ✅ Add `Meta` class BEFORE `__str__` (Django style guide)
4. ✅ Index on `organization_id` (auto via mixin, but verify)
5. ✅ Add to `apps/<app>/admin.py` if needed

### Before Modifying Middleware
1. ✅ Order matters: `JWTAuth` → `Tenant` → `RLS` → `RateLimit`
2. ✅ Don't break `request.user` flow (allauth dependency)
3. ✅ Test with: anonymous, authenticated, multi-org user, superuser

### Before Writing a Migration
1. ✅ Use `migration-validator` subagent
2. ✅ For RLS: use `RESET app.X` not `SET app.X = NULL` (PostgreSQL syntax)
3. ✅ For raw SQL: include tenant filter or document why bypassing
4. ✅ Add indexes for new FKs explicitly
5. ✅ Run `make sqlmigrate` to preview SQL

### Before Adding Dependency
1. ✅ Check Django/Python version compatibility (e.g., `django-celery-beat` < 2.8 doesn't support Django 5.2)
2. ✅ Add to correct file: `base.txt` (always), `dev.txt` (testing), `prod.txt` (deployment-only)
3. ✅ If imported in `conftest.py` — must be in `dev.txt` (CI fails otherwise)

### Before Pushing
1. ✅ Pre-push hook runs automatically: lint + Django check + pytest unit/integration/security
2. ✅ Don't `--no-verify` unless explicitly justified
3. ✅ Branch protection blocks direct push to `main` (use feature branches + PR)

---

## 🎯 Tooling

### Daily Commands
```bash
make run                       # dev server (port 8013)
make migrate                   # apply migrations
make test                      # full pytest
make test-security             # only tenant boundary tests
/home/hsm/scripts/start-postgres.sh   # local PostgreSQL daemon
```

### Slash Commands (this project)
```
/verify        — run all CI locally (lint + tests + frontend build)
/ship          — verify + commit + push + PR
/new-task NAME — create feature/<NAME> branch + skeleton
/audit-tenant  — security: check tenant isolation across models
/db-snapshot   — PostgreSQL backup
```

### Subagents (this project)
```
migration-validator  — Django migration + RLS safety
tenant-auditor       — cross-tenant data leak detection
test-runner          — smart pytest scope (changed files only)
```

### Skills (auto-invoked)
```
multitenant-rls    — TenantManager + RLS patterns
auth-jwt           — JWT + device fingerprinting flow
```

---

## 📋 Env Variables (.env)

| Variable | Default | Note |
|----------|---------|------|
| `DJANGO_ENV` | `dev` | dev / prod |
| `SECRET_KEY` | — | required, never commit |
| `DB_NAME` | `yuzdanyuz_db` | PostgreSQL DB |
| `DB_USER` | `hsm` | PG user |
| `DB_HOST` | `127.0.0.1` | NOT `localhost` (DNS resolves to public IP on this server) |
| `DB_PORT` | `5992` | non-standard port |
| `REDIS_URL` | `redis://...:6379/1` | with password |
| `ENABLE_RLS` | `true` | RLS enforcement |

`.env` is gitignored. Use `.env.example` as template.

---

## 📊 Repo Structure

```
yuzdanyuz-monorepo/
├── .claude/                    ← agent setup (this directory)
├── .github/workflows/ci.yml    ← 4 parallel jobs
├── .pre-commit-config.yaml     ← local quality gate
├── packages/
│   ├── backend/                Django 5.2 (port 8013)
│   ├── frontend-web/           Next.js 15 (port 3000)
│   ├── frontend-mobile/        React Native + Expo
│   └── shared/                 TypeScript types
└── docs/                       cross-package documentation
```

Backend internal structure: see `packages/backend/` (apps/, core/, tests/, requirements/, docs/).

---

## 🔗 Reference Files (Source of Truth)

When in doubt, read these (don't guess from memory):
- `packages/backend/core/middleware/rls_middleware.py` — RLS context setup
- `packages/backend/core/tenant.py` — `tenant_context`, `get_current_org`
- `packages/backend/core/managers.py` — TenantManager, GlobalManager
- `packages/backend/apps/catalog/migrations/0003_enable_rls.py` — RLS migration template
- `packages/backend/docs/roadmap.md` — task progress + deliverables
- `.pre-commit-config.yaml` — quality gate definitions
- `.github/workflows/ci.yml` — CI definition (mirror in `/verify`)

---

## ✍️ Communication Style

- **Default language**: Uzbek (user preference)
- **Code/commits**: English (international convention)
- **Comments**: Only WHY (non-obvious), never WHAT
- **Concision**: 1-2 sentence summaries; no narration
- **Tone**: Match user's directness; flag risks, never lecture

---

## 🚫 Hard Stops (Block These)

- ❌ Hardcoded secrets in any file (.env only, gitignored)
- ❌ Raw SQL without tenant filter or explicit comment
- ❌ `--no-verify` push (always justify)
- ❌ Direct push to `main` (branch protection enforces)
- ❌ Models without `organization` FK (unless `Tag`-like global)
- ❌ Bare `except:` clauses (use `except Exception as e:` minimum)
- ❌ `print()` for logging (use `logging` module)
- ❌ `SET app.X = NULL` SQL (use `RESET app.X`)

---

## ✅ Quick Status Check

Run before starting any task:
```bash
git status                                  # clean tree?
git log --oneline -3                        # recent commits
gh run list --limit 1                       # CI green?
cat .claude/LESSONS.md | head -50           # recent lessons
```
