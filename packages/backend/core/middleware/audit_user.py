"""ISSUE-409 — AuditUserMiddleware.

Per-request actor'ni ContextVar'ga yozadi (`core.audit_user`). `AuditUserMixin`
shu yerdan `created_by/updated_by` FK'lariga avtomatik qo'yadi.

Order: JWTAuth → Tenant → **AuditUser** → RLS → ... (request.user mavjud bo'lishi
shart, JWTAuth'dan keyin).
"""

from core.audit_user import clear_current_user, set_current_user


class AuditUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False):
            set_current_user(user)
        try:
            return self.get_response(request)
        finally:
            clear_current_user()
