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
