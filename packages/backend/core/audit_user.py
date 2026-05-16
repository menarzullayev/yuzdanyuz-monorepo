"""ISSUE-409 — Per-request actor (current user) ContextVar.

`AuditUserMixin.save()` shu yerdan `current_user`'ni o'qiydi va
created_by/updated_by FK'larini avtomatik to'ldiradi.

Pattern: TenantMiddleware'dagi ContextVar bilan bir xil — `AuditUserMiddleware`
har request boshida set qiladi, oxirida clear qiladi. Background worker
(Celery, management command) `with audit_user_context(user): ...` ishlatadi.

Anonymous request → None → mixin FK NULL qoldiradi (SOC2 audit trail "system" mark).
"""

from contextlib import contextmanager
from contextvars import ContextVar

_current_user: ContextVar = ContextVar('audit_current_user', default=None)


def get_current_user():
    """Joriy actor (CustomUser yoki None — anonymous/system)."""
    return _current_user.get()


def set_current_user(user) -> None:
    """Middleware request boshida chaqiradi."""
    _current_user.set(user)


def clear_current_user() -> None:
    """Middleware request oxirida chaqiradi (memory leak'ni oldini olish)."""
    _current_user.set(None)


@contextmanager
def audit_user_context(user):
    """Worker/management command/test'da explicit actor o'rnatish.

    with audit_user_context(admin):
        organization.update_settings(...)
    """
    token = _current_user.set(user)
    try:
        yield
    finally:
        _current_user.reset(token)
