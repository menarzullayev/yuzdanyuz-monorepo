"""
Tenant context — thread-local storage orqali joriy organization ni saqlaydi.

Ishlatish:
    from core.tenant import get_current_org, set_current_org, tenant_context

    # View yoki service ichida:
    org = get_current_org()

    # Test yoki management command ichida:
    with tenant_context(org):
        Question.objects.all()  # faqat shu org savollari

Unscoped (admin/worker uchun, tenant filter'siz):
    from core.tenant import unscoped_context
    with unscoped_context():
        Question.objects.all()  # barcha tenantlar (faqat explicit holatda)
"""

import threading
from contextlib import contextmanager

_thread_locals = threading.local()


def get_current_org():
    """Joriy thread dagi aktiv organization ni qaytaradi. Yo'q bo'lsa None."""
    return getattr(_thread_locals, 'current_org', None)


def set_current_org(org):
    """Joriy thread uchun aktiv organization ni o'rnatadi."""
    _thread_locals.current_org = org


def clear_current_org():
    """Thread local ni tozalaydi. Middleware request tugaganda chaqiradi."""
    _thread_locals.current_org = None


@contextmanager
def tenant_context(org):
    """
    Test va management command larda tenant ni vaqtinchalik o'rnatish uchun.

    with tenant_context(org):
        qs = Question.objects.all()   # faqat org savollari
    # blokdan chiqqanda avtomatik tozalanadi
    """
    previous = get_current_org()
    set_current_org(org)
    try:
        yield
    finally:
        set_current_org(previous)


# ── Unscoped context (admin/worker bypass) ────────────────────
# Default'da TenantManager fail-closed: org=None bo'lsa empty queryset.
# Bu marker explicit ravishda "men barcha tenantlar ma'lumotini olishni xohlayman"
# deb belgilashga imkon beradi (admin, background worker, bootstrap script).


def is_unscoped_allowed() -> bool:
    return getattr(_thread_locals, 'unscoped_allowed', False)


def set_unscoped_allowed(allowed: bool):
    _thread_locals.unscoped_allowed = allowed


@contextmanager
def unscoped_context():
    """
    Tenant filter'ni explicit bypass qilish.

    with unscoped_context():
        Question.objects.all()   # barcha tenantlar

    Ishlatish joylari:
        - Admin'da cross-tenant ko'rinish
        - Celery background worker
        - Bootstrap / data migration script
        - TenantMiddleware ichida _resolve_org (chicken-and-egg)
    """
    previous = is_unscoped_allowed()
    set_unscoped_allowed(True)
    try:
        yield
    finally:
        set_unscoped_allowed(previous)
