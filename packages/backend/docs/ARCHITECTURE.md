# Milliy Sertifikat — Architecture Overview

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     User's Browser                       │
│              (Chrome, Safari, Mobile)                    │
└──────────────────┬──────────────────────────────────────┘
                   │ HTTPS
                   ▼
┌─────────────────────────────────────────────────────────┐
│         Web Server (Nginx / Apache)                      │
│         https://hsm.sammu.uz/yuzdanyuz/                 │
└──────────────────┬──────────────────────────────────────┘
                   │ HTTP (reverse proxy)
                   ▼
┌─────────────────────────────────────────────────────────┐
│  Django 5.2 Application Server (127.0.0.1:8013)         │
│  ┌─────────────────────────────────────────────────────┐│
│  │ HTMX + Alpine.js Frontend (templating)              ││
│  ├─────────────────────────────────────────────────────┤│
│  │ Request/Response Cycle                              ││
│  │ • Middleware (Tenant context, Auth, CORS)           ││
│  │ • URL Router (apps/*/urls.py)                       ││
│  │ • Views (Function + Class-based)                    ││
│  │ • Serializers (JSON responses)                      ││
│  └─────────────────────────────────────────────────────┘│
└──────────────────┬───────────────┬──────────────────────┘
                   │               │
          ┌────────▼──────┐   ┌────▼─────────────┐
          │ PostgreSQL DB │   │ Redis Cache      │
          │ (multi-tenant)│   │ (sessions, jobs) │
          │ Socket conn   │   │ Port 6379        │
          │ :5992         │   │ Password: ***    │
          └───────────────┘   └──────────────────┘
                   │
          ┌────────▼──────────────┐
          │  ClickHouse (OLAP)    │
          │  (analytics events)   │
          └───────────────────────┘
```

## Core Architecture Principles

### 1. Multi-Tenancy (Logical Isolation)

**Strategy**: Single Database + Organization Filtering

```python
# Every model inherits from TenantTimestampMixin
from core.mixins import TenantTimestampMixin

class Question(TenantTimestampMixin):
    organization = ForeignKey(Organization, on_delete=models.CASCADE)
    # Auto-filters by current_org in TenantManager
    objects = TenantManager()  # Auto-filters
    global_objects = GlobalManager()  # Bypass filter (admin only)
```

**Flow**:
```
User Request
    ↓
Middleware (TenantMiddleware)
    ↓
Sets tenant_context(org) in thread-local
    ↓
Model.objects.all() → automatically adds WHERE organization_id = X
    ↓
Response
    ↓
Clears tenant_context
```

**Key Files**:
- `core/middleware.py` — TenantMiddleware
- `core/tenant.py` — tenant_context, get_current_org()
- `core/managers.py` — TenantManager, GlobalManager
- `core/mixins.py` — TenantTimestampMixin

### 2. Request/Response Flow (HTMX-based)

**Typical HTMX Pattern**:

```html
<!-- Frontend (template) -->
<div id="draft-list-panel"
     hx-get="/catalog/drafts/"
     hx-trigger="load, draftListReload from:body"
     hx-swap="innerHTML">
  Loading...
</div>

<!-- Alpine.js state -->
<div x-data="{ selectedDrafts: [] }">
  <button @click="selectedDrafts.push(id)">Select</button>
</div>
```

**Backend (views.py)**:
```python
@require_http_methods(["GET"])
def draft_list_htmx(request, batch_id):
    # Tenant is auto-set by middleware
    drafts = Question.objects.filter(batch_id=batch_id)
    # Returns partial HTML, NOT JSON
    return render(request, 'catalog/partials/draft_list.html', {
        'drafts': drafts,
        'filter_tabs': [...]
    })
```

### 3. Authentication & Authorization

**Layers**:

| Layer | Mechanism | Usage |
|-------|-----------|-------|
| **Layer 1** | Django built-in | `is_staff`, `is_superuser` (platform admin) |
| **Layer 2** | OrgRole.permissions | `user.has_org_permission(org, 'exams.create')` |
| **Fallback** | RLS (PostgreSQL) | Row-level security (extra protection) |

**User Type** (derived property):
```python
@property
def user_type(self):
    if self.is_superuser:
        return 'platform_admin'
    if self.is_staff:
        return 'platform_staff'

    membership = self.membership_set.filter(organization=current_org).first()
    return membership.role.name if membership else 'b2c'
```

### 4. Data Models Structure (Domain-Driven Design)

```
apps/
├── accounts/
│   ├── models.py → CustomUser, Region, District
│   └── views.py → Login, Register, Profile
│
├── organizations/
│   ├── models.py → Organization, OrgRole, Membership, OrgInvite
│   └── signals.py → Create default roles
│
├── catalog/
│   ├── models.py → Question, QuestionBank, Tag, ImportBatch, QuestionDraft
│   ├── services.py → AI Parser, validators
│   └── views.py → Import, Review, Publish workflows
│
├── exams/
│   ├── models.py → MockExam, PracticeSession, ExamAttempt, UserAnswer
│   └── views.py → Exam engine, anti-cheat
│
├── intelligence/
│   ├── models.py → SkillTag, UserSkillProfile, StudyPlan, AIFeedback
│   └── tasks.py → Celery: knowledge graph computation
│
├── commerce/
│   ├── models.py → Wallet, WalletTransaction, Subscription, Affiliate
│   └── views.py → Checkout, wallet top-up
│
├── engagement/
│   ├── models.py → Streak, League, Badge, Notification
│   └── tasks.py → Celery: daily streak check, league reset
│
└── analytics/
    ├── models.py → ClickHouseEvent (metadata only, real data in ClickHouse)
    ├── tasks.py → Celery: event streaming
    └── views.py → B2B dashboards
```

### 5. Database Connection Strategy

**Why Socket, Not TCP?**
```
TCP (127.0.0.1:5432) → Requires listening on localhost interface
Socket (/tmp/.s.PGSQL.5432) → Direct IPC, faster, more secure
```

**.env Configuration**:
```bash
DB_HOST=/home/hsm/.local/pgsql/run   # Socket directory
DB_PORT=5992                         # Socket port
DB_NAME=yuzdanyuz_db
DB_USER=hsm
DB_PASSWORD=hsm_secret_pass
```

**Django Settings**:
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env('DB_NAME'),
        'USER': env('DB_USER'),
        'PASSWORD': env('DB_PASSWORD'),
        'HOST': env('DB_HOST'),  # Socket path
        'PORT': env('DB_PORT'),
    }
}
```

### 6. Async Task Processing (Celery)

**Architecture**:

```
Django View
    ↓
Task queued to Redis
    ↓
Celery Worker picks up task
    ↓
Async execution (don't block request)
    ↓
Result stored in Redis
    ↓
Frontend polls or receives WebSocket update
```

**Common Tasks**:
- `import_questions.py:parse_excel_to_drafts()` — Bulk import
- `intelligence.py:compute_skill_profile()` — Knowledge graph
- `engagement.py:check_daily_streaks()` — Gamification (Celery Beat)
- `analytics.py:stream_event_to_clickhouse()` — Analytics

### 7. Caching Strategy (Redis)

**Cache Keys Pattern**:
```python
# User sessions
f"session:{user_id}:{device_fingerprint}"

# Draft cache (during review)
f"draft:{draft_id}:autosave"

# Leaderboard (real-time)
f"leaderboard:global"
f"leaderboard:region:{region_id}"
f"leaderboard:org:{org_id}"

# Rate limiting
f"ratelimit:{user_id}:exam_start"
```

**TTL Strategy**:
```
Sessions: 15 min (access) + 30 days (refresh)
Draft autosave: 5 min
Leaderboard: 1 hour
Rate limit: 60 seconds
```

### 8. Security Layers

**1. Request Level**:
```python
# middleware.py
class TenantMiddleware:
    # Sets organization context
    # Filters all queries by organization_id

class DeviceFingerprintMiddleware:
    # Checks device fingerprint (browser + OS + IP)
    # One user = one device at a time
```

**2. Database Level**:
```sql
-- PostgreSQL RLS (Row Level Security)
ALTER TABLE catalog_question ENABLE ROW LEVEL SECURITY;

CREATE POLICY org_isolation ON catalog_question
  USING (organization_id = current_setting('app.current_org_id')::uuid);
```

**3. API Level**:
```python
# views.py
@require_http_methods(["POST"])
@require_organization_permission('exams.create')
def create_exam(request):
    # Only org members with permission can create
```

---

## Key Architectural Files

| File | Purpose |
|------|---------|
| `core/middleware.py` | Tenant context setup |
| `core/managers.py` | TenantManager (auto-filtering) |
| `core/mixins.py` | TenantTimestampMixin base class |
| `core/tenant.py` | tenant_context context manager |
| `apps/*/models.py` | Domain models (inherit TenantTimestampMixin) |
| `apps/*/views.py` | HTMX endpoints (partial HTML responses) |
| `templates/base.html` | Master template (Alpine.js init) |
| `templates/*/partial/` | HTMX swap targets |

---

## Critical Design Decisions

1. **HTMX over SPA**: Minimal JavaScript, server-side templating
2. **Logical over Physical Tenants**: Simpler ops, shared resources
3. **Partial HTML over JSON**: Template reuse, CSRF tokens built-in
4. **Alpine.js for Interactivity**: Lightweight state management
5. **Redis for Sessions**: Device fingerprinting + rate limiting
6. **Celery for Async**: Heavy lifting (AI parsing, notifications)
7. **PostgreSQL Socket**: Local development security + performance
