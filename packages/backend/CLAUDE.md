# Milliy Sertifikat — CLAUDE.md

## Loyiha haqida
O'zbekiston miqyosidagi EdTech Super-App: DTM simulyatori + AI diagnostika + B2B white-label.

**Stack**: Django 5.2 + HTMX + Alpine.js + Celery + Redis + PostgreSQL + ClickHouse

## Muhit sozlash

```bash
source venv/bin/activate
cp .env.example .env   # kerakli o'zgaruvchilarni to'ldiring
make migrate
make run
```

## Arxitektura: asosiy qoidalar

### 1. Multi-Tenancy — Logical Isolation
- Barcha tenant ma'lumotlari bitta DB da, `organization_id` orqali ajratilgan
- Har bir tenant-ega model `TenantTimestampMixin` dan meros oladi:
  ```python
  from core.mixins import TenantTimestampMixin
  class Question(TenantTimestampMixin):
      ...
  ```
- `objects` → `TenantManager` (avtomatik `organization` filter)
- `global_objects` → `GlobalManager` (filter bypass, faqat admin/worker uchun)

### 2. Tenant Context
```python
from core.tenant import tenant_context, get_current_org

# Test va management command:
with tenant_context(org):
    Question.objects.all()   # faqat shu org savollari

# View da — TenantMiddleware avtomatik o'rnatadi
```

### 3. App tuzilmasi (Domain-Driven Design)
```
apps/
  accounts/      — CustomUser, Region, District
  organizations/ — Organization, OrgRole, Membership, OrgInvite
  catalog/       — Question, QuestionBank, Tag, AnswerChoice
  exams/         — MockExam, PracticeSession, ExamAttempt, UserAnswer
  intelligence/  — SkillTag, UserSkillProfile, StudyPlan, AIFeedback
  commerce/      — Wallet, WalletTransaction, Subscription, Affiliate
  engagement/    — Streak, League, Badge, Notification
  analytics/     — ClickHouseEvent, Report, B2BDashboard
```

### 4. RBAC — Ikki qatlam
- **Layer 1**: Django built-in (`is_staff`, `is_superuser`) — platform admin uchun
- **Layer 2**: `OrgRole.permissions = JSONField(list)` — org darajasida
  ```python
  user.has_org_permission(org, 'exams.create')
  # Yoki '*' wildcard — barcha permission
  ```

### 5. user_type — derived property
`CustomUser.user_type` DB da saqlanmaydi, Membership dan hisoblanadi:
- `platform_admin` — is_superuser
- `platform_staff` — is_staff
- `b2c` — faol Membership yo'q
- else — `membership.role.name`

## Env o'zgaruvchilari

| O'zgaruvchi | Tavsif | Default |
|-------------|--------|---------|
| `DJANGO_ENV` | `dev` yoki `prod` | `dev` |
| `SECRET_KEY` | Django secret key | — (majburiy) |
| `DB_NAME` | PostgreSQL DB nomi | `yuzdanyuz_db` |
| `DB_USER` | PostgreSQL user | `postgres` |
| `DB_PASSWORD` | PostgreSQL parol | `""` |
| `DB_HOST` | PostgreSQL host | `127.0.0.1` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `PROJECT_BRAND_NAME` | Brend nomi | `Milliy Sertifikat` |

## Foydali buyruqlar

```bash
make run            # dev server
make migrate        # migratsiya qo'llash
make makemigrations # yangi migratsiya
make shell          # Django shell (shell_plus agar o'rnatilgan)
make test           # testlar
make setup-rls      # PostgreSQL RLS (faqat production)
```

## Roadmap

Batafsil: [docs/roadmap.md](docs/roadmap.md)

| Task | Holat |
|------|-------|
| 1 — Foundation & Multi-Tenant | ✅ Tugallandi |
| 2 — Auth + Device Fingerprinting | ✅ Tugallandi |
| 3–10 | Rejada |
