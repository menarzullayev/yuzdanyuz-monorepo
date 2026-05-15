---
name: multitenant-rls
description: Multi-tenant data isolation patterns for Django + PostgreSQL Row Level Security. Use when adding tenant-aware models, writing migrations that touch tenant tables, debugging cross-tenant data leaks, or designing tenant-aware queries.
---

# Multi-Tenant + RLS Patterns

This project uses **3-layer tenant isolation**:
1. App-level: `TenantManager` auto-filter by `organization_id`
2. Context: `tenant_context(org)` thread-local org binding
3. DB-level: PostgreSQL RLS with `current_setting('app.current_org_id')::uuid`

---

## Adding a Tenant-Aware Model

**Always**:
```python
# packages/backend/apps/<app>/models.py
from core.mixins import TenantTimestampMixin

class Question(TenantTimestampMixin):
    text = models.TextField()
    # organization FK + created_at + updated_at auto-added
    # objects = TenantManager (auto-filter)
    # global_objects = GlobalManager (admin bypass)

    class Meta:
        verbose_name = _('Savol')
        verbose_name_plural = _('Savollar')

    def __str__(self):
        return self.text[:50]
```

**Index on organization_id**: auto-added by mixin, but for composite queries add:
```python
class Meta:
    indexes = [
        models.Index(fields=['organization', 'created_at']),
        models.Index(fields=['organization', 'subject']),
    ]
```

**Global model exception** (no tenant): `Tag`, `Subject` (when shared platform-wide). Don't inherit `TenantTimestampMixin`. Document why.

---

## Querying Tenant Data

### View context (auto)
TenantMiddleware sets context from JWT `current_org` claim:
```python
# In views — no special code needed
def my_view(request):
    questions = Question.objects.all()   # auto-filtered to request.org
```

### Test/management command (explicit)
```python
from core.tenant import tenant_context

with tenant_context(org):
    questions = Question.objects.all()
    # ↑ filtered to org

# Outside context: returns ALL (admin-only behavior)
all_qs = Question.global_objects.all()
```

### Switching context
```python
with tenant_context(org_a):
    a_count = Question.objects.count()

with tenant_context(org_b):
    b_count = Question.objects.count()
# a_count and b_count are DIFFERENT — that's the point
```

---

## RLS Migration Pattern

For NEW tenant table:
```python
# apps/<app>/migrations/000X_enable_rls_<table>.py
from django.db import migrations


def enable_rls(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    cursor.execute("ALTER TABLE myapp_mymodel ENABLE ROW LEVEL SECURITY;")
    cursor.execute("""
        CREATE POLICY "mymodel_org_select" ON myapp_mymodel
        FOR SELECT USING (
            organization_id = current_setting('app.current_org_id')::uuid
        );
    """)
    cursor.execute("""
        CREATE POLICY "mymodel_org_insert" ON myapp_mymodel
        FOR INSERT WITH CHECK (
            organization_id = current_setting('app.current_org_id')::uuid
        );
    """)
    cursor.execute("""
        CREATE POLICY "mymodel_org_update" ON myapp_mymodel
        FOR UPDATE USING (
            organization_id = current_setting('app.current_org_id')::uuid
        );
    """)


def disable_rls(apps, schema_editor):
    cursor = schema_editor.connection.cursor()
    for policy in ['mymodel_org_select', 'mymodel_org_insert', 'mymodel_org_update']:
        cursor.execute(f'DROP POLICY IF EXISTS "{policy}" ON myapp_mymodel;')
    cursor.execute("ALTER TABLE myapp_mymodel DISABLE ROW LEVEL SECURITY;")


class Migration(migrations.Migration):
    dependencies = [('myapp', '000Y_previous')]
    operations = [migrations.RunPython(enable_rls, disable_rls)]
```

**Reference**: `packages/backend/apps/catalog/migrations/0003_enable_rls.py`

---

## Anti-Patterns (DO NOT)

### ❌ Raw SQL without tenant filter
```python
# WRONG — bypasses TenantManager
cursor.execute("SELECT * FROM apps_question WHERE id = %s", [qid])
```

### ❌ Setting NULL via SET
```python
# WRONG — PostgreSQL syntax error (LESSONS.md #01)
cursor.execute("SET app.current_org_id = NULL;")

# CORRECT
cursor.execute("RESET app.current_org_id;")
```

### ❌ Cross-tenant FK
```python
# WRONG — User can reference Org B from Org A's model
class Submission(TenantTimestampMixin):
    question = models.ForeignKey(Question, ...)   # ⚠️ Question.org might differ from self.org

# CORRECT — validate in clean()
def clean(self):
    if self.question.organization != self.organization:
        raise ValidationError("Cross-tenant FK forbidden")
```

### ❌ `global_objects` in normal views
```python
# WRONG — leaks data across tenants
def list_view(request):
    return Question.global_objects.all()   # ⚠️ admin bypass in user view!

# CORRECT — global_objects only for admin/celery worker
```

---

## Verification

```bash
# Tenant boundary tests (should always pass)
cd packages/backend
./venv/bin/pytest tests/security/test_tenant_boundary.py -v

# Manual check
./venv/bin/python manage.py shell
>>> from apps.organizations.models import Organization
>>> from apps.catalog.models import Question
>>> from core.tenant import tenant_context
>>> org_a = Organization.objects.get(slug='org-a')
>>> org_b = Organization.objects.get(slug='org-b')
>>> with tenant_context(org_a):
...     a_count = Question.objects.count()
>>> with tenant_context(org_b):
...     b_count = Question.objects.count()
>>> assert a_count != b_count   # different = isolation works
```

---

## Reference Files

- `packages/backend/core/middleware/rls_middleware.py` — RLS context setup
- `packages/backend/core/tenant.py` — `tenant_context`, `get_current_org`
- `packages/backend/core/managers.py` — `TenantManager`, `GlobalManager`
- `packages/backend/core/mixins.py` — `TenantTimestampMixin`
- `packages/backend/apps/catalog/migrations/0003_enable_rls.py` — RLS migration template
- `packages/backend/tests/security/test_tenant_boundary.py` — boundary tests
- `.claude/LESSONS.md` #01 — RLS NULL syntax fix
