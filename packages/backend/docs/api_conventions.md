# API Conventions

> ISSUE-202, 204, 206 — backend API contract standards.

---

## 1. File layout (ISSUE-202)

Each app separates request handlers by **client type**:

```
apps/<app>/
├── views/
│   ├── __init__.py     re-export shortcuts (back-compat)
│   ├── htmx.py         HTMX partials (form-encoded POST, returns templates)
│   ├── api.py          REST API (JSON, returns Response with envelope)
│   └── webhook.py      External webhooks (signature-verified, AllowAny)
├── urls/
│   ├── __init__.py     combine v1 + htmx
│   ├── htmx.py         /<app>/...  (browser-facing)
│   ├── api_v1.py       /api/v1/<app>/...  (REST)
│   └── webhook.py      /api/webhooks/<provider>/
└── serializers.py      shared (HTMX uses .data, API uses serializer instance)
```

**Status (2026-05-16)**: convention defined, full migration incremental.
- ✅ `accounts/views/` already uses sub-modules (auth.py, otp.py, linking.py, login_views.py)
- 🔵 Other apps: single views.py, refactor in future PRs (one app per sprint)

---

## 2. URL versioning (ISSUE-203)

REST API path: `/api/v1/<domain>/...`. Old unversioned paths kept with
`Sunset` + `Deprecation` header (6 months notice).

```python
# core/urls.py
path('api/v1/exams/', include('apps.exams.urls', namespace='exams-v1')),
path('api/exams/', include('apps.exams.urls', namespace='exams')),  # deprecated
```

Versioning bumps:
- **v1 → v2**: breaking changes (field renames, response shape changes).
- **patch within v1**: additive only (new optional fields, new endpoints).

---

## 3. Response envelope (ISSUE-204)

### Success (per-endpoint shape, no global wrapper)

Endpoint-specific shape, e.g.:
```json
{
    "count": 42,
    "results": [...],
    "next": null,
    "previous": null
}
```

### Error (unified via `core.exceptions.unified_exception_handler`)

```json
{
    "success": false,
    "error": {
        "code": "validation_error",
        "message": "Email format noto'g'ri",
        "details": {"email": ["Enter a valid email address."]}
    },
    "detail": "Email format noto'g'ri"  // backward-compat
}
```

Error codes (machine-readable):

| Code | HTTP | When |
|---|---|---|
| `validation_error` | 400 | Field-level validation failed |
| `authentication_required` | 401 | No/expired JWT cookie |
| `permission_denied` | 403 | RBAC check failed |
| `not_found` | 404 | Resource missing |
| `conflict` | 409 | Idempotency clash (e.g. attempt already exists) |
| `rate_limit_exceeded` | 429 | RateLimitMiddleware tripped |
| `server_error` | 500 | Unhandled exception (Sentry alert) |
| `service_unavailable` | 503 | Readiness probe failed (DB/Redis down) |

---

## 4. Field naming (ISSUE-206)

### REST API serializers

- FK fields: use `<name>_id` suffix in serialized output:
  ```python
  class ExamAttemptSerializer(serializers.ModelSerializer):
      mock_exam_id = serializers.PrimaryKeyRelatedField(
          source='mock_exam', queryset=MockExam.objects.all(),
      )
  ```

- Boolean fields: `is_<adjective>` (e.g. `is_published`, `is_correct`)
- Timestamp fields: `<verb>_at` (e.g. `created_at`, `submitted_at`)
- Counts: `<noun>_count` (e.g. `correct_count`, `total_points`)
- Snake_case (DRF default) in JSON; never camelCase.

### Special case — DRF `PrimaryKeyRelatedField`

DRF's `PrimaryKeyRelatedField` defaults to the **bare** attribute name
(no `_id` suffix). To enforce convention, always use `source` + `_id`:

```python
# BAD (DRF default — discovered via Lesson 15)
question_version = serializers.PrimaryKeyRelatedField(...)

# GOOD (ISSUE-206 convention)
question_version_id = serializers.PrimaryKeyRelatedField(
    source='question_version', queryset=QuestionVersion.objects.all()
)
```

OpenAPI schema (`drf-spectacular`) documents the expected name → frontend
discovery via Swagger UI.

---

## 5. WebSocket authentication (ISSUE-207)

Consumer's `connect()` MUST explicitly validate JWT cookie:

```python
from core.ws_auth import authenticate_ws

class MyConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = await authenticate_ws(self.scope)
        if user is None:
            await self.close(code=4401)
            return
        self.scope['user'] = user
        ...
```

Close codes:
- `4401` — unauthenticated (no/invalid token)
- `4403` — authenticated but not authorized for this resource
- `4404` — resource not found
- `4409` — conflict (e.g., attempt not active)

Never rely on `self.scope.get('user')` set by middleware alone — middleware
refactors may silently break this.

---

## 6. State machine transitions (ISSUE-205)

Models with `status` enum (ExamAttempt, MockExam, PaymentIntent, etc.) MUST
expose explicit transition methods:

```python
class ExamAttempt(SoftDeleteMixin, TenantTimestampMixin):
    _ALLOWED_TRANSITIONS = {
        Status.IN_PROGRESS: {Status.SUBMITTED, Status.CANCELLED, ...},
        Status.SUBMITTED: {Status.DISPUTED},
        ...
    }

    def submit(self) -> None:
        self._validate_transition(self.Status.SUBMITTED)
        self.status = self.Status.SUBMITTED
        self.save(...)

    def cancel_for_cheating(self, reason) -> None:
        ...
```

Views/services NEVER set `attempt.status = ...` directly — always via
transition method. ValueError raised on invalid transition.

**Status (2026-05-16)**: ExamAttempt migrated. Others:
- 🔵 PaymentIntent, OrganizationSubscription, QuestionDispute, Notification,
  MockExam, PracticeSession — incremental.

---

## 7. OpenAPI schema (ISSUE-201)

`drf-spectacular` auto-generates schema from view + serializer code:
- Schema: `GET /api/schema/`
- Swagger UI: `GET /api/schema/swagger/`
- Redoc: `GET /api/schema/redoc/`

Each view should use `@extend_schema(...)` for examples/responses/tags:

```python
from drf_spectacular.utils import extend_schema, OpenApiExample

class WalletView(APIView):
    @extend_schema(
        tags=['commerce'],
        summary='Get current user wallet balance',
        responses={200: WalletSerializer},
    )
    def get(self, request):
        ...
```

CI validates schema: `python manage.py spectacular --validate --fail-on-warn`.

---

## 8. Logging & Retention (ISSUE-X06)

- Use `core.logging.log_event(level, event_name, **fields)` for structured events.
- `event` MUST be snake_case noun_verb (e.g. `payment_failed`, `streak_broken`).
- All IDs (`user_id`, `org_id`, `attempt_id`) MUST be passed as string (UUID-safe).
- Never use f-string interpolation for production events — aggregators (Loki /
  CloudWatch / Datadog) cannot index inline values, so queries like
  `event=payment_failed AND org_id=<uuid>` will not work.

Example:

```python
from core.logging import log_event

log_event('info', 'exam_submitted',
          user_id=str(user.id),
          org_id=str(org.id),
          score=92.5,
          duration_seconds=1800)
```

The `yuzdanyuz.events` logger is wired to a JSON handler in both `base.py`
LOGGING and `core.observability.json_logging_dict()` (prod), so fields become
top-level JSON keys ready for indexing.

**Retention policy** (production):

| Level | Retention | Storage |
|---|---|---|
| INFO | 30 days | Loki / CloudWatch |
| WARNING+ | 90 days | Loki / CloudWatch |
| ERROR/CRITICAL | 1 year | S3 cold + Sentry |

---

## 9. Audit Log Retention (ISSUE-401)

`django-simple-history` `historical_<model>` jadvallarda har CRUD operatsiyani
saqlaydi. Har row'da: `history_date`, `history_user` (kim qildi), `history_type`
(`+` create, `~` update, `-` delete), va modelning to'liq snapshot'i.

**Registered models** (2026-05-16):

| Model | Historical table | Retention | Asos |
|---|---|---|---|
| `commerce.Wallet` | `historicalwallet` | **7 yil** | UZ buxgalteriya talabi |
| `commerce.OrganizationSubscription` | `historicalorganizationsubscription` | **7 yil** | Financial reconciliation |
| `commerce.SubscriptionPlan` | `historicalsubscriptionplan` | **7 yil** | Price change audit |
| `accounts.CustomUser` | `historicalcustomuser` | **5 yil** | SOC2 identity |
| `organizations.Membership` | `historicalmembership` | **5 yil** | SOC2 access control |
| `organizations.OrgRole` | `historicalorgrole` | **5 yil** | SOC2 RBAC changes |
| `organizations.Organization` | `historicalorganization` | **5 yil** | Tenant lifecycle |
| `catalog.Question` | `historicalquestion` | **2 yil** | Content audit |
| `exams.ExamAttempt` | `historicalexamattempt` | **2 yil** | DTM compliance |

**Retention enforcement**: monthly Celery task `clean_historical_records`
calls `python manage.py clean_old_history --days=N` per model.

**PII redaction**: When user requests GDPR erasure, history rows for that user
get `history_user=NULL` + PII fields blanked (separate task, ISSUE-402).

**Excluded fields**: `CustomUser.password` and `CustomUser.last_login` are
not tracked — har login historical row yaratmasligi uchun va parol hash
audit log'da ko'rinmasligi uchun.

**Middleware**: `simple_history.middleware.HistoryRequestMiddleware`
`AuditUserMiddleware` dan keyin (so `request.user` allaqachon resolve qilingan
bo'lsin) MIDDLEWARE ro'yxatida joylashgan.
